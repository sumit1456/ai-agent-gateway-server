"""Customer result webhooks and their durable Postgres delivery queue."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import or_
from sqlmodel import select

from app.db import SessionLocal
from app.models.tables import WebhookDelivery, WebhookEndpoint
from app.security import decrypt, encrypt

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8
RETRY_DELAYS_SECONDS = (5, 15, 60, 300, 900, 1800, 3600)


def new_signing_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


async def validate_endpoint_url(url: str) -> str:
    """Accept public HTTPS callback URLs and reject obvious local destinations."""
    value = url.strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Webhook URL must be a public HTTPS URL without embedded credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Webhook URL cannot point to a local or internal host.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (not address.is_global):
        raise ValueError("Webhook URL must use a public IP address.")
    if not address:
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo, host, parsed.port or 443, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise ValueError("Webhook host could not be resolved.") from exc
        addresses = {record[4][0] for record in records}
        if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise ValueError("Webhook host must resolve only to public IP addresses.")
    if parsed.port and not (1 <= parsed.port <= 65535):
        raise ValueError("Webhook URL has an invalid port.")
    return value


def make_delivery(endpoint: WebhookEndpoint, user_id: UUID, event_type: str,
                  payload: dict, run_id: UUID | None = None) -> WebhookDelivery:
    return WebhookDelivery(
        user_id=user_id,
        run_id=run_id,
        event_type=event_type,
        endpoint_url=endpoint.url,
        # Snapshot the secret so a later rotation does not invalidate queued messages.
        signing_secret_enc=endpoint.signing_secret_enc,
        payload={"id": str(uuid4()), "type": event_type, "created_at": datetime.now(timezone.utc).isoformat(), **payload},
    )


async def _claim_one() -> WebhookDelivery | None:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=2)
    async with SessionLocal() as db:
        statement = (
            select(WebhookDelivery)
            .where(
                or_(
                    (WebhookDelivery.status == "pending") & (WebhookDelivery.next_attempt_at <= now),
                    (WebhookDelivery.status == "processing") & (WebhookDelivery.updated_at < stale),
                )
            )
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        delivery = (await db.exec(statement)).first()
        if not delivery:
            return None
        delivery.status = "processing"
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.updated_at = now
        await db.commit()
        return delivery


async def _send(delivery: WebhookDelivery) -> tuple[int | None, str | None]:
    body = json.dumps(delivery.payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    secret = decrypt(delivery.signing_secret_enc).encode("utf-8")
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AI-Agent-Gateway-Webhooks/1.0",
        "X-AGW-Event": delivery.event_type,
        "X-AGW-Delivery": str(delivery.id),
        "X-AGW-Signature": f"sha256={digest}",
    }
    try:
        await validate_endpoint_url(delivery.endpoint_url)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            async with client.stream("POST", delivery.endpoint_url, content=body, headers=headers) as response:
                if 200 <= response.status_code < 300:
                    return response.status_code, None
                return response.status_code, f"Receiver returned HTTP {response.status_code}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:900]}"


async def _deliver_one() -> bool:
    delivery = await _claim_one()
    if not delivery:
        return False
    status_code, error = await _send(delivery)
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        current = await db.get(WebhookDelivery, delivery.id)
        if not current:
            return True
        current.last_status_code = status_code
        current.last_error = error
        current.updated_at = now
        if error is None:
            current.status = "delivered"
            current.delivered_at = now
        elif current.attempts >= MAX_ATTEMPTS:
            current.status = "failed"
        else:
            current.status = "pending"
            delay = RETRY_DELAYS_SECONDS[min(current.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
            current.next_attempt_at = now + timedelta(seconds=delay)
        await db.commit()
    if error:
        logger.warning("Webhook delivery %s failed (attempt %s): %s", delivery.id, delivery.attempts, error)
    return True


async def delivery_worker() -> None:
    """Single lightweight worker; row locking permits safe multi-process deployment."""
    while True:
        try:
            processed = await _deliver_one()
            if not processed:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Webhook delivery worker iteration failed")
            await asyncio.sleep(2)


async def enqueue_test_delivery(endpoint: WebhookEndpoint, user_id: UUID) -> UUID:
    delivery = make_delivery(
        endpoint, user_id, "webhook.test",
        {"message": "Webhook endpoint test succeeded if you received this."},
    )
    async with SessionLocal() as db:
        db.add(delivery)
        await db.commit()
    return delivery.id
