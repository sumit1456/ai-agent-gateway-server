from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from app.security import current_user
from app.db import get_db
from app.models.tables import User, Agent, Provider, AgentSession, Run, Artifact
from app.models.agent_config import AgentConfig
from typing import List
import uuid

router = APIRouter()


@router.get("/default-prompts", response_model=dict)
async def get_default_prompts():
    """
    Get all default system prompts for reference.
    
    Users can use these as a starting point for customization.
    Returns the default prompts for router, planner, executor, reviewer, direct, and finalizer.
    """
    from app.engine.prompts import (
        ROUTER_SYSTEM, PLANNER_SYSTEM, EXECUTOR_SYSTEM,
        REVIEWER_SYSTEM, DIRECT_SYSTEM, FINALIZER_SYSTEM
    )
    
    return {
        "default_prompts": {
            "router": {
                "prompt": ROUTER_SYSTEM,
                "description": "Decides whether to use 'direct' mode (simple) or 'plan' mode (complex multi-step)",
                "variables": []
            },
            "planner": {
                "prompt": PLANNER_SYSTEM,
                "description": "Breaks down complex tasks into steps",
                "variables": ["{max_steps}"]
            },
            "executor": {
                "prompt": EXECUTOR_SYSTEM,
                "description": "Executes individual steps of a plan",
                "variables": []
            },
            "reviewer": {
                "prompt": REVIEWER_SYSTEM,
                "description": "Reviews completed steps and decides if they meet success criteria",
                "variables": []
            },
            "direct": {
                "prompt": DIRECT_SYSTEM,
                "description": "Handles simple queries in a single pass",
                "variables": []
            },
            "finalizer": {
                "prompt": FINALIZER_SYSTEM,
                "description": "Composes the final answer from step results",
                "variables": []
            }
        },
        "builtin_tools": [
            "read_artifact", "list_artifacts", "remember_fact",
            "get_session_memory", "store_kv", "get_kv",
            "get_conversation_history", "kb_search"
        ],
        "note": "Custom prompts are optional. If not provided, these defaults are used. Built-in tools are always available to agents."
    }


async def _resolve_provider(db, user_id, provider_ref: str) -> Provider | None:
    """Find a configured provider by UUID or provider name (e.g. 'nvidia')."""
    try:
        p_uuid = uuid.UUID(str(provider_ref))
        result = await db.exec(
            select(Provider).where(
                Provider.user_id == user_id,
                Provider.id == p_uuid
            )
        )
        p = result.first()
        if p:
            return p
    except (ValueError, TypeError):
        pass

    result = await db.exec(
        select(Provider).where(
            Provider.user_id == user_id,
            Provider.provider == str(provider_ref)
        )
    )
    return result.first()

@router.post("/", response_model=dict)
async def create_agent(
    config: AgentConfig,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Create a new agent configuration."""
    provider = await _resolve_provider(db, current_user.id, config.provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider not found or not authorized"
        )
    
    agent_data = config.model_dump(mode="json")
    agent_data["provider_id"] = str(provider.id)

    # Create the agent
    agent = Agent(
        user_id=current_user.id,
        provider_id=provider.id,
        name=config.name,
        config=agent_data
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    return {
        "id": str(agent.id),
        "name": agent.name,
        "provider_id": str(agent.provider_id),
        "config": agent.config,
        "created_at": agent.created_at.isoformat()
    }

@router.get("/", response_model=List[dict])
async def list_agents(
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """List all agents for the current user."""
    result = await db.exec(
        select(Agent).where(Agent.user_id == current_user.id)
    )
    agents = result.all()
    
    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            "provider_id": str(agent.provider_id),
            "config": agent.config,
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None
        }
        for agent in agents
    ]

@router.get("/{agent_id}", response_model=dict)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Get a specific agent configuration."""
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent ID format"
        )
    
    result = await db.exec(
        select(Agent).where(
            Agent.user_id == current_user.id,
            Agent.id == agent_uuid
        )
    )
    agent = result.first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    return {
        "id": str(agent.id),
        "name": agent.name,
        "provider_id": str(agent.provider_id),
        "config": agent.config,
        "created_at": agent.created_at.isoformat(),
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None
    }

@router.put("/{agent_id}", response_model=dict)
async def update_agent(
    agent_id: str,
    config: AgentConfig,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Update an agent configuration."""
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent ID format"
        )
    
    provider = await _resolve_provider(db, current_user.id, config.provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider not found or not authorized"
        )
    
    # Get existing agent
    result = await db.exec(
        select(Agent).where(
            Agent.user_id == current_user.id,
            Agent.id == agent_uuid
        )
    )
    agent = result.first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Update the agent
    from datetime import datetime, timezone
    agent.provider_id = provider.id
    agent.name = config.name
    agent_data = config.model_dump(mode="json")
    agent_data["provider_id"] = str(provider.id)
    agent.config = agent_data
    agent.updated_at = datetime.now(timezone.utc)  # Update timestamp
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    return {
        "id": str(agent.id),
        "name": agent.name,
        "provider_id": str(agent.provider_id),
        "config": agent.config,
        "updated_at": agent.updated_at.isoformat()
    }

@router.delete("/{agent_id}", response_model=dict)
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Delete an agent."""
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent ID format"
        )
    
    result = await db.exec(
        select(Agent).where(
            Agent.user_id == current_user.id,
            Agent.id == agent_uuid
        )
    )
    agent = result.first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Cascade delete child runs and their artifacts
    runs_result = await db.exec(
        select(Run).where(Run.agent_id == agent_uuid)
    )
    for run in runs_result.all():
        artifacts_result = await db.exec(
            select(Artifact).where(Artifact.run_id == run.id)
        )
        for artifact in artifacts_result.all():
            await db.delete(artifact)
        await db.delete(run)

    # Cascade delete child sessions
    sessions_result = await db.exec(
        select(AgentSession).where(AgentSession.agent_id == agent_uuid)
    )
    for session in sessions_result.all():
        await db.delete(session)

    await db.delete(agent)
    await db.commit()
    
    return {
        "message": f"Agent {agent_id} deleted successfully"
    }