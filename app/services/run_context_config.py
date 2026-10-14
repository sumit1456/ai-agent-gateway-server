"""Load and cache the stable database-backed configuration used to start a run."""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID

from sqlalchemy import and_
from sqlmodel import select

from app.models.tables import KnowledgeBase, Provider, Tool

log = logging.getLogger(__name__)


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _provider_data(record: Provider | None) -> dict | None:
    if not record:
        return None
    return {
        "id": str(record.id),
        "provider": record.provider,
        # Keep credentials encrypted in Redis; decrypt only when constructing the run.
        "api_key_enc": record.api_key_enc,
        "enabled_models": record.enabled_models,
        "default_model": record.default_model,
    }


def _knowledge_base_data(record: KnowledgeBase) -> dict:
    return {
        "id": str(record.id),
        "namespace": record.namespace,
        "vector_api_key_enc": record.vector_api_key_enc,
        "vector_index_host": record.vector_index_host,
        "rerank_model": record.rerank_model,
    }


def _tool_data(record: Tool) -> dict:
    from app.security import encrypt

    return {
        "id": str(record.id),
        "name": record.name,
        "description": record.description,
        "kind": record.kind,
        "endpoint_url": record.endpoint_url,
        # Tool API rows may contain a plaintext legacy token, so add an
        # encryption layer before writing the snapshot to Redis.
        "auth_enc": encrypt(record.auth_enc) if record.auth_enc else None,
        "auth_cache_wrapped": True,
        "timeout_ms": record.timeout_ms,
    }


async def load_agent_execution_config(
    db,
    *,
    user_id: UUID,
    agent_id: str | None,
    provider_ref: str,
    knowledge_base_ids: list[str] | None,
    tool_names: list[str] | None,
) -> dict:
    """Return provider, attached KB, and tool config using one SQL statement.

    The result is a JSON-safe snapshot. Redis is an optional read-through cache;
    database remains authoritative and cache failures never block a run.
    """
    from app.redis_client import get_agent_execution_config, set_agent_execution_config

    cache_inputs = {
        "provider_ref": str(provider_ref),
        "knowledge_base_ids": [str(value) for value in knowledge_base_ids or []],
        "tool_names": sorted({str(name) for name in (tool_names or []) if name}),
    }
    signature = hashlib.sha256(json.dumps(cache_inputs, sort_keys=True).encode()).hexdigest()[:20]
    cache_id = f"{agent_id}:{signature}" if agent_id else None
    cached = await get_agent_execution_config(str(user_id), cache_id)
    if cached is not None:
        return cached

    provider_uuid = _uuid_or_none(provider_ref)
    kb_uuids = [parsed for value in knowledge_base_ids or [] if (parsed := _uuid_or_none(value))]
    names = cache_inputs["tool_names"]

    provider_match = (
        and_(Provider.user_id == user_id, Provider.id == provider_uuid)
        if provider_uuid
        else and_(Provider.user_id == user_id, Provider.provider == str(provider_ref))
    )
    # SQLAlchemy compiles IN([]) to a false predicate, so empty attachments still
    # preserve one query without accidentally joining every KB or tool.
    kb_match = and_(KnowledgeBase.user_id == user_id, KnowledgeBase.id.in_(kb_uuids))
    tool_match = and_(Tool.user_id == user_id, Tool.name.in_(names))

    statement = (
        select(Provider, KnowledgeBase, Tool)
        .select_from(Provider)
        .outerjoin(KnowledgeBase, kb_match)
        .outerjoin(Tool, tool_match)
        .where(provider_match)
    )
    result = await db.exec(statement)
    provider_data = None
    knowledge_bases: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    for provider, kb, tool in result.all():
        provider_data = _provider_data(provider)
        if kb is not None:
            knowledge_bases[str(kb.id)] = _knowledge_base_data(kb)
        if tool is not None:
            tools[tool.name] = _tool_data(tool)

    payload = {
        "provider": provider_data,
        "knowledge_bases": list(knowledge_bases.values()),
        "tools": list(tools.values()),
    }
    await set_agent_execution_config(str(user_id), cache_id, payload)
    return payload
