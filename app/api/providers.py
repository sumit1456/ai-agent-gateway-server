from __future__ import annotations
from typing import List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from app.security import current_user, encrypt, decrypt
from app.db import get_db
from app.models.tables import (
    User,
    Provider,
    Agent,
    KnowledgeBase,
    Run,
    Artifact,
    AgentSession,
    KbDocument,
)
from app.providers.registry import list_providers, get_provider

router = APIRouter()


class ProviderIn(BaseModel):
    provider_id: str
    api_key: str
    enabled_models: Optional[List[str]] = None
    default_model: Optional[str] = None


class ProviderUpdate(BaseModel):
    api_key: Optional[str] = None
    enabled_models: Optional[List[str]] = None
    default_model: Optional[str] = None


def _catalog() -> list[dict]:
    return [
        {
            "provider_id": p.provider_id,
            "display_name": p.display_name,
        }
        for pid in list_providers()
        for p in [get_provider(pid)]
    ]


def _configured_row(r: Provider) -> dict:
    return {
        "id": str(r.id),
        "provider_id": r.provider,
        "enabled_models": r.enabled_models or [],
        "default_model": r.default_model,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _find_user_provider(db, user_id, provider_id: str) -> Provider | None:
    result = await db.exec(
        select(Provider).where(
            Provider.user_id == user_id,
            Provider.provider == provider_id,
        )
    )
    return result.first()


def _require_provider(provider_id: str):
    try:
        return get_provider(provider_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {provider_id}",
        )


@router.get("/", response_model=List[dict])
async def list_available_providers():
    """List all available LLM providers (public catalog)."""
    return _catalog()


@router.get("/configured", response_model=List[dict])
async def list_configured_providers(
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """List the current user's configured providers (never returns keys)."""
    result = await db.exec(
        select(Provider).where(Provider.user_id == current_user.id)
    )
    return [_configured_row(r) for r in result.all()]


@router.post("/", response_model=dict)
async def add_provider(
    payload: ProviderIn,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """Configure a provider: validate the API key live, then store it encrypted."""
    provider = _require_provider(payload.provider_id)

    if not await provider.validate_api_key(payload.api_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid API key",
        )

    if await _find_user_provider(db, current_user.id, payload.provider_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {payload.provider_id} already configured",
        )

    provider_record = Provider(
        user_id=current_user.id,
        provider=payload.provider_id,
        api_key_enc=encrypt(payload.api_key),
        enabled_models=payload.enabled_models,
        default_model=payload.default_model,
    )
    db.add(provider_record)
    await db.commit()
    await db.refresh(provider_record)

    return {
        "message": "Provider API key stored successfully",
        **_configured_row(provider_record),
    }


@router.get("/{provider_id}", response_model=dict)
async def get_provider_config(
    provider_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """Get the configured provider for the current user."""
    _require_provider(provider_id)
    provider = await _find_user_provider(db, current_user.id, provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not configured for this user",
        )
    # Note: the actual API key is never returned
    return _configured_row(provider)


@router.get("/{provider_id}/models", response_model=dict)
async def list_provider_models(
    provider_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """Proxy the provider's /models endpoint using the user's stored key."""
    provider = _require_provider(provider_id)

    record = await _find_user_provider(db, current_user.id, provider_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not configured for this user",
        )

    api_key = decrypt(record.api_key_enc)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(provider.models_url(), headers=provider.auth_headers(api_key))
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach {provider.display_name}: {e.__class__.__name__}",
        )

    if r.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"{provider.display_name} model list request failed "
                f"({r.status_code}). The API key may lack permission."
            ),
        )

    try:
        data = r.json()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Invalid response from {provider.display_name}",
        )

    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    model_ids = sorted(
        {
            m["id"]
            for m in items
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]
        }
    )
    return {"provider_id": provider_id, "models": model_ids}


@router.put("/{provider_id}", response_model=dict)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """Update an existing provider: rotate the API key and/or model selection."""
    provider = _require_provider(provider_id)

    existing = await _find_user_provider(db, current_user.id, provider_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not configured for this user",
        )

    if payload.api_key is not None:
        if not await provider.validate_api_key(payload.api_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid API key",
            )
        existing.api_key_enc = encrypt(payload.api_key)

    if payload.enabled_models is not None:
        existing.enabled_models = payload.enabled_models
    if payload.default_model is not None:
        existing.default_model = payload.default_model

    db.add(existing)
    await db.commit()
    await db.refresh(existing)

    return {
        "message": "Provider updated successfully",
        **_configured_row(existing),
    }


@router.delete("/{provider_id}", response_model=dict)
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """Delete a provider API key."""
    _require_provider(provider_id)
    provider = await _find_user_provider(db, current_user.id, provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider_id} not configured for this user",
        )

    # Cascade delete any agents referencing this provider
    agents_result = await db.exec(
        select(Agent).where(
            Agent.user_id == current_user.id,
            Agent.provider_id == provider.id,
        )
    )
    for agent in agents_result.all():
        runs_result = await db.exec(
            select(Run).where(Run.agent_id == agent.id)
        )
        for run in runs_result.all():
            artifacts_result = await db.exec(
                select(Artifact).where(Artifact.run_id == run.id)
            )
            for artifact in artifacts_result.all():
                await db.delete(artifact)
            await db.delete(run)

        sessions_result = await db.exec(
            select(AgentSession).where(AgentSession.agent_id == agent.id)
        )
        for session in sessions_result.all():
            await db.delete(session)

        await db.delete(agent)

    # Cascade delete any knowledge bases referencing this provider
    kbs_result = await db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.provider_id == provider.id,
        )
    )
    for kb in kbs_result.all():
        docs_result = await db.exec(
            select(KbDocument).where(KbDocument.kb_id == kb.id)
        )
        for doc in docs_result.all():
            await db.delete(doc)
        await db.delete(kb)

    await db.delete(provider)
    await db.commit()

    return {"message": f"Provider {provider_id} deleted successfully"}
