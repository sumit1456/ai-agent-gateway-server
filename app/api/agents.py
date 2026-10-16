from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from app.security import current_user
from app.db import get_db
from app.models.tables import User, Agent, Provider, AgentSession, Run, Artifact, WorkflowVersion, Workflow
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
    from app.engine.prompt_templates import ROLE_PROMPT_TEMPLATES, ROLE_PROMPT_PRESETS
    
    return {
        "default_prompts": ROLE_PROMPT_TEMPLATES,
        "role_presets": ROLE_PROMPT_PRESETS,
        "builtin_tools": [
            "read_artifact", "list_artifacts", "remember_fact",
            "get_session_memory", "store_kv", "get_kv",
            "get_conversation_history", "semantic_search"
        ],
        "note": "Role presets and additive instructions are optional. Core role duties remain active unless an advanced full prompt override is supplied. Built-in tools are always available to agents."
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
    workflow_version = await _get_owned_workflow_version(db, current_user.id, config.workflow_version_id)
    agent_data["workflow_version_id"] = str(workflow_version.id) if workflow_version else None

    # Create the agent
    agent = Agent(
        user_id=current_user.id,
        provider_id=provider.id,
        workflow_version_id=workflow_version.id if workflow_version else None,
        name=config.name,
        config=agent_data
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    from app.redis_client import set_agent_prompt_config, invalidate_agent_execution_configs
    await set_agent_prompt_config(str(current_user.id), str(agent.id), agent.config or {})
    await invalidate_agent_execution_configs(str(current_user.id))
    
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

    workflow_version = await _get_owned_workflow_version(db, current_user.id, config.workflow_version_id)
    
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
    agent.workflow_version_id = workflow_version.id if workflow_version else None
    agent.name = config.name
    agent_data = config.model_dump(mode="json")
    agent_data["provider_id"] = str(provider.id)
    agent_data["workflow_version_id"] = str(workflow_version.id) if workflow_version else None
    agent.config = agent_data
    agent.updated_at = datetime.now(timezone.utc)  # Update timestamp
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    from app.redis_client import invalidate_agent_prompt_config, set_agent_prompt_config, invalidate_agent_execution_configs
    await invalidate_agent_prompt_config(str(current_user.id), str(agent.id))
    await set_agent_prompt_config(str(current_user.id), str(agent.id), agent.config or {})
    await invalidate_agent_execution_configs(str(current_user.id))
    
    return {
        "id": str(agent.id),
        "name": agent.name,
        "provider_id": str(agent.provider_id),
        "config": agent.config,
        "updated_at": agent.updated_at.isoformat()
    }


async def _get_owned_workflow_version(db, user_id, version_id):
    if not version_id:
        return None
    try:
        version_uuid = uuid.UUID(str(version_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow version ID")
    result = await db.exec(
        select(WorkflowVersion)
        .join(Workflow, Workflow.id == WorkflowVersion.workflow_id)
        .where(WorkflowVersion.id == version_uuid, Workflow.user_id == user_id, WorkflowVersion.is_active == True)
    )
    version = result.first()
    if not version:
        raise HTTPException(status_code=400, detail="Workflow version not found or not owned by user")
    return version

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
    from app.redis_client import invalidate_agent_prompt_config, invalidate_agent_execution_configs
    await invalidate_agent_prompt_config(str(current_user.id), str(agent.id))
    await invalidate_agent_execution_configs(str(current_user.id))
    
    return {
        "message": f"Agent {agent_id} deleted successfully"
    }
