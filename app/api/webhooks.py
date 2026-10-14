from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.db import get_db
from app.models.tables import User, WebhookDelivery, WebhookEndpoint
from app.security import current_user, encrypt
from app.webhooks import enqueue_test_delivery, new_signing_secret, validate_endpoint_url

router = APIRouter()


class WebhookEndpointInput(BaseModel):
    url: str = Field(..., min_length=10, max_length=2048)


def _endpoint_response(endpoint: WebhookEndpoint | None) -> dict:
    if not endpoint:
        return {"configured": False, "endpoint": None}
    return {
        "configured": True,
        "endpoint": {
            "id": str(endpoint.id),
            "url": endpoint.url,
            "enabled": endpoint.enabled,
            "secret_prefix": "whsec_…" if endpoint.signing_secret_enc else None,
            "created_at": endpoint.created_at.isoformat(),
            "updated_at": endpoint.updated_at.isoformat(),
        },
    }


@router.get("")
async def get_webhook(user: User = Depends(current_user), db=Depends(get_db)):
    endpoint = (await db.exec(select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id))).first()
    return _endpoint_response(endpoint)


@router.put("")
async def save_webhook(payload: WebhookEndpointInput, user: User = Depends(current_user), db=Depends(get_db)):
    try:
        url = await validate_endpoint_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    endpoint = (await db.exec(select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id))).first()
    created = endpoint is None
    secret = new_signing_secret() if created else None
    if created:
        endpoint = WebhookEndpoint(user_id=user.id, url=url, signing_secret_enc=encrypt(secret))
        db.add(endpoint)
    else:
        endpoint.url = url
        endpoint.enabled = True
        endpoint.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(endpoint)
    result = _endpoint_response(endpoint)
    result["signing_secret"] = secret
    result["secret_notice"] = "Save this secret now. It is shown only when first created or rotated." if secret else None
    return result


@router.post("/rotate-secret")
async def rotate_webhook_secret(user: User = Depends(current_user), db=Depends(get_db)):
    endpoint = (await db.exec(select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id))).first()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Configure a webhook endpoint first.")
    secret = new_signing_secret()
    endpoint.signing_secret_enc = encrypt(secret)
    endpoint.updated_at = datetime.now(timezone.utc)
    pending = (await db.exec(select(WebhookDelivery).where(
        WebhookDelivery.user_id == user.id,
        WebhookDelivery.status.in_(["pending", "processing"]),
    ))).all()
    for delivery in pending:
        delivery.signing_secret_enc = endpoint.signing_secret_enc
        delivery.updated_at = endpoint.updated_at
    await db.commit()
    return {**_endpoint_response(endpoint), "signing_secret": secret, "secret_notice": "Save this secret now. It will not be shown again."}


@router.post("/test", status_code=202)
async def test_webhook(user: User = Depends(current_user), db=Depends(get_db)):
    endpoint = (await db.exec(select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id))).first()
    if not endpoint or not endpoint.enabled:
        raise HTTPException(status_code=404, detail="Configure an enabled webhook endpoint first.")
    delivery_id = await enqueue_test_delivery(endpoint, user.id)
    return {"delivery_id": str(delivery_id), "status": "queued"}


@router.delete("")
async def delete_webhook(user: User = Depends(current_user), db=Depends(get_db)):
    endpoint = (await db.exec(select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id))).first()
    if endpoint:
        await db.delete(endpoint)
        await db.commit()
    return {"deleted": True}


@router.get("/deliveries")
async def list_webhook_deliveries(limit: int = 50, user: User = Depends(current_user), db=Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = (await db.exec(
        select(WebhookDelivery).where(WebhookDelivery.user_id == user.id)
        .order_by(WebhookDelivery.created_at.desc()).limit(limit)
    )).all()
    return {"deliveries": [
        {
            "id": str(row.id), "run_id": str(row.run_id) if row.run_id else None,
            "event_type": row.event_type, "status": row.status, "attempts": row.attempts,
            "last_status_code": row.last_status_code, "last_error": row.last_error,
            "created_at": row.created_at.isoformat(),
            "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        } for row in rows
    ]}


@router.post("/deliveries/{delivery_id}/retry", status_code=202)
async def retry_webhook_delivery(delivery_id: str, user: User = Depends(current_user), db=Depends(get_db)):
    try:
        delivery_uuid = UUID(delivery_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid delivery ID.") from exc
    delivery = (await db.exec(select(WebhookDelivery).where(
        WebhookDelivery.id == delivery_uuid, WebhookDelivery.user_id == user.id
    ))).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Webhook delivery not found.")
    if delivery.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed deliveries can be retried.")
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.next_attempt_at = datetime.now(timezone.utc)
    delivery.last_error = None
    delivery.last_status_code = None
    delivery.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"delivery_id": str(delivery.id), "status": "queued"}
