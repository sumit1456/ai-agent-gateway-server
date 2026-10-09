from fastapi import APIRouter, Depends, HTTPException, status, Body, Header
from sqlmodel import select
from pydantic import BaseModel, Field
from app.security import current_user
from app.db import get_db
from app.models.tables import User, Run, Agent, AgentSession
from app.models.agent_config import AgentConfig
from app.engine.runner import execute_run
from typing import List, Optional
import uuid
import asyncio

router = APIRouter()


class RunRequest(BaseModel):
    """Request model for creating a run."""
    input: str = Field(..., min_length=1, max_length=10000, description="The input text for the agent")
    variables: Optional[dict] = Field(default=None, description="Optional variables to pass to the agent")


@router.get("/builtin-tools", response_model=dict)
async def list_builtin_tools():
    """
    List all built-in platform tools that are automatically available to agents.
    
    These tools are non-negotiable and always registered for the agent to use.
    Users can see what tools are available but cannot disable them.
    """
    from app.tools.builtin import (
        ReadArtifactTool, ListArtifactsTool, KBSearchTool, RememberFactTool,
        GetSessionMemoryTool, StoreKVTool, GetKVTool, GetConversationHistoryTool
    )
    
    # Create dummy context just to get tool descriptions
    class DummyCtx:
        def __init__(self):
            self.retriever = None
            self.kb_namespaces = []
    
    dummy_ctx = DummyCtx()
    
    tools_info = [
        {
            "name": "read_artifact",
            "description": "Read the full content of a previously stored artifact by its ID.",
            "category": "internal",
            "always_available": True
        },
        {
            "name": "list_artifacts",
            "description": "List all artifacts produced so far in this run.",
            "category": "internal",
            "always_available": True
        },
        {
            "name": "remember_fact",
            "description": "Store a short, durable finding as a fact for this run.",
            "category": "memory",
            "always_available": True
        },
        {
            "name": "get_session_memory",
            "description": "Read durable facts remembered across previous runs of this session.",
            "category": "memory",
            "always_available": True
        },
        {
            "name": "store_kv",
            "description": "Persist a key-value pair for this agent (survives across all runs).",
            "category": "persistence",
            "always_available": True
        },
        {
            "name": "get_kv",
            "description": "Read a value previously stored with store_kv.",
            "category": "persistence",
            "always_available": True
        },
        {
            "name": "get_conversation_history",
            "description": "Retrieve conversation history from previous runs in this session.",
            "category": "memory",
            "always_available": True
        },
        {
            "name": "kb_search",
            "description": "Search the knowledge base(s) attached to this agent for relevant information.",
            "category": "knowledge",
            "always_available": False,
            "requires": "Knowledge base attached to agent"
        }
    ]
    
    return {
        "builtin_tools": tools_info,
        "total_count": len(tools_info),
        "note": "These tools are automatically available to all agents and cannot be disabled. They enable memory, persistence, and knowledge retrieval capabilities."
    }


@router.post("/agents/{agent_id}/run", response_model=dict)
async def create_run(
    agent_id: str,
    payload: RunRequest,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
    x_end_user_id: Optional[str] = Header(None, alias="X-End-User-ID")  # NEW: Header support
):
    """
    Create a new run for an agent.
    
    Returns immediately with run_id and status='running'.
    Poll GET /v1/runs/{run_id} to check status and get results.
    
    Recommended polling interval: 1-2 seconds
    
    Headers (optional):
        X-End-User-ID: Identifier for your end-user (for multi-tenant tracking)
    
    Request Body:
    {
        "input": "Your question or task here",
        "variables": {"key": "value"}  // optional
    }
    """
    input = payload.input
    variables = payload.variables
    end_user_id = x_end_user_id  # Use header value
    # Validate input
    if not input or not input.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Input cannot be empty"
        )
    
    if len(input) > 10000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Input too long (max 10,000 characters)"
        )
    
    # Validate variables if provided
    if variables:
        if not isinstance(variables, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Variables must be a JSON object"
            )
        if len(str(variables)) > 5000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Variables too large (max 5,000 characters)"
            )
    
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent ID format"
        )
    
    # Verify the agent exists and belongs to the user
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
    
    # Get or create a session for this agent and end-user
    # If end_user_id is not provided, use "default" for backward compatibility
    effective_end_user_id = end_user_id or "default"
    
    result = await db.exec(
        select(AgentSession).where(
            AgentSession.agent_id == agent.id,
            AgentSession.end_user_id == effective_end_user_id,
            AgentSession.external_session_id == None  # For now, we don't use external session IDs
        )
    )
    session = result.first()
    if not session:
        session = AgentSession(
            agent_id=agent.id,
            user_id=current_user.id,
            end_user_id=effective_end_user_id,
            external_session_id=None,
            facts={}
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
    
    # Generate a run ID
    run_id = str(uuid.uuid4())
    
    # Start the run in the background
    # Note: In a real implementation, we would properly handle the agent config
    # For now, we'll use a simplified config
    agent_cfg = agent.config if isinstance(agent.config, dict) else {}
    agent_models = agent_cfg.get("models") or {}
    if isinstance(agent_models, str):
        agent_models = {"planner": agent_models, "executor": agent_models, "reviewer": agent_models}

    config_dict = {
        "name": agent.name,
        "provider_id": str(agent.provider_id),
        "models": {
            "planner": agent_models.get("planner", "meta-llama/llama-3.1-70b-instruct"),
            "executor": agent_models.get("executor", "meta-llama/llama-3.1-70b-instruct"),
            "reviewer": agent_models.get("reviewer", "meta-llama/llama-3.1-8b-instruct")
        },
        "system_prompt": agent_cfg.get("system_prompt", "You are a helpful assistant."),
        "variables": list(agent_cfg.get("variables", {}).keys()) if isinstance(agent_cfg.get("variables"), dict) else list(agent_cfg.get("variables", [])),
        "tools": list(agent_cfg.get("tools", [])),
        "knowledge_base_ids": list(agent_cfg.get("knowledge_base_ids", [])),
        "temperature": agent_cfg.get("temperature", 0.3),
        "limits": agent_cfg.get("limits") or {
            "max_iterations": 5,
            "max_tokens_per_run": 50000,
            "timeout_s": 120,
            "max_steps": 8,
            "max_parallel_steps": 3,
            "max_tool_turns": 4,
            "max_tool_output_chars": 4000,
            "llm_review": True
        }
    }
    
    # Execute the run in the background
    asyncio.create_task(execute_run(
        run_id=run_id,
        user_id=str(current_user.id),
        config_dict=config_dict,
        input_str=input,
        variables=variables or {},
        agent_id=str(agent.id),
        session_id=str(session.id),
        end_user_id=effective_end_user_id  # Pass end_user_id to runner
    ))
    
    # Create initial run record in database
    run_record = Run(
        id=uuid.UUID(run_id),
        agent_id=agent.id,
        user_id=current_user.id,
        session_id=session.id,
        end_user_id=effective_end_user_id,  # Store end_user_id
        input=input,
        variables=variables or {},
        status="running"
    )
    db.add(run_record)
    await db.commit()
    
    return {
        "run_id": run_id,
        "status": "running",
        "message": "Run started successfully. Poll GET /v1/runs/{run_id} for status."
    }

@router.get("/runs/{run_id}", response_model=dict)
async def get_run(
    run_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Get the status and results of a run."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run ID format"
        )
    
    result = await db.exec(
        select(Run).where(
            Run.user_id == current_user.id,
            Run.id == run_uuid
        )
    )
    run = result.first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    response = {
        "id": str(run.id),
        "agent_id": str(run.agent_id),
        "status": run.status,
        "input": run.input,
        "variables": run.variables,
        "result": run.result,
        "stop_reason": run.stop_reason,
        "tokens_used": run.tokens_used,
        "duration_ms": run.duration_ms,
        "created_at": run.created_at.isoformat()
    }
    
    # Add helpful error context if failed
    if run.status == "failed" and run.result and "error" in run.result:
        response["error_type"] = "execution_error"
        response["error_message"] = run.result["error"]
    
    return response

@router.get("/runs", response_model=dict)
async def list_runs(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """
    List runs for the current user with pagination.
    
    Args:
        limit: Max runs to return (default: 50, max: 100)
        offset: Number of runs to skip (default: 0)
        status: Filter by status: running, done, failed (optional)
    """
    # Validate limit
    if limit > 100:
        limit = 100
    if limit < 1:
        limit = 1
    
    # Build query
    query = select(Run).where(Run.user_id == current_user.id)
    
    if status:
        query = query.where(Run.status == status)
    
    query = query.order_by(Run.created_at.desc()).limit(limit).offset(offset)
    
    result = await db.exec(query)
    runs = result.all()
    
    # Get total count
    from sqlalchemy import func, select as sa_select
    count_query = sa_select(func.count()).select_from(Run).where(Run.user_id == current_user.id)
    if status:
        count_query = count_query.where(Run.status == status)
    
    total_result = await db.exec(count_query)
    total = total_result.one()
    
    return {
        "runs": [
            {
                "id": str(run.id),
                "agent_id": str(run.agent_id),
                "status": run.status,
                "created_at": run.created_at.isoformat(),
                "duration_ms": run.duration_ms,
                "tokens_used": run.tokens_used
            }
            for run in runs
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }

@router.post("/runs/{run_id}/cancel", response_model=dict)
async def cancel_run(
    run_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Cancel a running job."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run ID format"
        )
    
    result = await db.exec(
        select(Run).where(
            Run.user_id == current_user.id,
            Run.id == run_uuid
        )
    )
    run = result.first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found"
        )
    
    if run.status not in ["running"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel run with status: {run.status}"
        )
    
    # In a real implementation, we would signal the run to cancel
    # For now, we'll just update the status
    run.status = "cancelled"
    run.stop_reason = "cancelled"
    db.add(run)
    await db.commit()
    
    return {
        "id": str(run.id),
        "status": run.status,
        "stop_reason": run.stop_reason,
        "message": "Run cancelled successfully"
    }

# SSE streaming will be implemented when Redis pub/sub is added
# For now, use polling: GET /v1/runs/{run_id}