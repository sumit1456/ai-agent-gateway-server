from fastapi import APIRouter, Depends, HTTPException, status, Body, Header
from fastapi.responses import StreamingResponse
from sqlmodel import select
from pydantic import BaseModel, Field
from app.security import current_user
from app.db import get_db
from app.models.tables import User, Run, Agent, AgentSession, ConversationMessage, WorkflowVersion, WorkflowRunStep
from app.models.agent_config import AgentConfig
from app.engine.runner import execute_run
from typing import Any, List, Optional
import uuid
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter()


class RunRequest(BaseModel):
    """Request model for creating a run."""
    input: Any = Field(..., description="Text or structured JSON input for the agent")
    variables: Optional[dict] = Field(default=None, description="Optional variables to pass to the agent")
    external_session_id: Optional[str] = Field(
        default=None, min_length=1, max_length=255,
        description="Caller-owned conversation ID. Reuse it to continue the same agent session.",
    )


@router.get("/builtin-tools", response_model=dict)
async def list_builtin_tools():
    """
    List all built-in platform tools that are automatically available to agents.
    
    These tools are non-negotiable and always registered for the agent to use.
    Users can see what tools are available but cannot disable them.
    """
    from app.tools.builtin import (
        ReadArtifactTool, ListArtifactsTool, SemanticSearchTool, RememberFactTool,
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
            "description": "On demand, retrieve up to 100 prior messages using last_n, optional role, or date filters. History is not automatically loaded into each run.",
            "category": "memory",
            "always_available": True
        },
        {
            "name": "search_conversation_memory",
            "description": "Semantically search older timestamped messages in the current session.",
            "category": "memory",
            "always_available": False,
            "requires": "Pinecone configured through an attached knowledge base"
        },
        {
            "name": "semantic_search",
            "description": "Find relevant passages by meaning in the knowledge base(s) attached to this agent.",
            "category": "knowledge",
            "always_available": False,
            "requires": "Knowledge base attached to agent"
        }
    ]
    
    return {
        "builtin_tools": tools_info,
        "total_count": len(tools_info),
        "note": "Tool availability depends on the agent workflow and attached resources. Knowledge search requires an attached knowledge base."
    }


@router.post("/agents/{agent_id}/run", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    agent_id: str,
    payload: RunRequest,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
    x_end_user_id: Optional[str] = Header(None, alias="X-End-User-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
):
    """
    Create a new run for an agent.
    
    Returns immediately with run_id and status='running'. Subscribe to
    GET /v1/runs/{run_id}/stream with the same bearer token to receive progress
    and the terminal result. GET /v1/runs/{run_id} remains available for polling.
    
    Headers (optional):
        X-End-User-ID: Identifier for your end-user (for multi-tenant tracking)
        X-Session-ID: Caller-owned conversation ID; reuse it to continue that session
    
    Request Body:
    {
        "input": "Your question or task here",
        "variables": {"key": "value"},  // optional
        "external_session_id": "conversation-123"  // optional; also accepted as X-Session-ID
    }
    """
    input = payload.input
    if not isinstance(input, (str, dict, list, int, float, bool)) or input is None:
        raise HTTPException(status_code=400, detail="Input must be text or a JSON value")
    input_text = input if isinstance(input, str) else json.dumps(input, ensure_ascii=False, separators=(",", ":"))
    variables = payload.variables
    end_user_id = x_end_user_id  # Use header value
    body_session_id = payload.external_session_id.strip() if payload.external_session_id else None
    header_session_id = x_session_id.strip() if x_session_id else None
    if body_session_id and header_session_id and body_session_id != header_session_id:
        raise HTTPException(status_code=400, detail="external_session_id and X-Session-ID must match")
    external_session_id = body_session_id or header_session_id
    if external_session_id == "":
        raise HTTPException(status_code=400, detail="Session ID cannot be empty")
    if external_session_id and len(external_session_id) > 255:
        raise HTTPException(status_code=400, detail="Session ID cannot exceed 255 characters")
    # Validate input
    if not input_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Input cannot be empty"
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
    
    request_config_started = time.perf_counter()
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

    from app.models.agent_config import Limits
    agent_limits = Limits.model_validate((agent.config or {}).get("limits") or {})
    if len(input_text) > agent_limits.max_input_chars:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input too long (max {agent_limits.max_input_chars:,} characters for this agent)"
        )
    
    # Get or create a session scoped to this owned agent, end-user, and caller session.
    effective_end_user_id = end_user_id or "default"
    
    result = await db.exec(
        select(AgentSession).where(
            AgentSession.agent_id == agent.id,
            AgentSession.end_user_id == effective_end_user_id,
            AgentSession.external_session_id == external_session_id,
        )
    )
    session = result.first()
    if not session:
        session = AgentSession(
            agent_id=agent.id,
            user_id=current_user.id,
            end_user_id=effective_end_user_id,
            external_session_id=external_session_id,
            facts={}
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
    
    # Generate a run ID
    run_id = str(uuid.uuid4())
    from app.services.detailed_trace import get_detailed_trace_logger
    detailed_trace = get_detailed_trace_logger(run_id)
    detailed_trace.record("user_input", {
        "input": input_text,
        "variables": variables or {},
        "agent_id": str(agent.id),
        "session_id": str(session.id),
        "end_user_id": effective_end_user_id,
    })

    # Start the run in the background
    # Note: In a real implementation, we would properly handle the agent config
    # For now, we'll use a simplified config
    from app.redis_client import get_agent_prompt_config, set_agent_prompt_config
    agent_cfg = await get_agent_prompt_config(str(current_user.id), str(agent.id))
    if agent_cfg is None:
        agent_cfg = agent.config if isinstance(agent.config, dict) else {}
        await set_agent_prompt_config(str(current_user.id), str(agent.id), agent_cfg)
    workflow_definition = None
    if agent.workflow_version_id:
        workflow_version = await db.get(WorkflowVersion, agent.workflow_version_id)
        if not workflow_version or not workflow_version.is_active:
            raise HTTPException(status_code=409, detail="Agent workflow version is missing or inactive")
        workflow_definition = workflow_version.definition
    if not workflow_definition:
        raise HTTPException(status_code=409, detail="Agent has no active workflow configured")
    agent_models = agent_cfg.get("models") or {}
    workflow_tool_names = {
        name
        for node in (workflow_definition or {}).get("nodes", [])
        for name in (node.get("tools") or [])
    }
    if isinstance(agent_models, str):
        agent_models = {"planner": agent_models, "executor": agent_models, "reviewer": agent_models}

    config_dict = {
        "name": agent.name,
        "provider_id": str(agent.provider_id),
        "workflow_version_id": str(agent.workflow_version_id) if agent.workflow_version_id else None,
        "workflow_definition": workflow_definition,
        "models": {
            "planner": agent_models.get("planner", "meta-llama/llama-3.1-70b-instruct"),
            "executor": agent_models.get("executor", "meta-llama/llama-3.1-70b-instruct"),
            "reviewer": agent_models.get("reviewer", "meta-llama/llama-3.1-8b-instruct"),
            "router": agent_models.get("router") or agent_models.get("executor", "meta-llama/llama-3.1-70b-instruct"),
            "direct": agent_models.get("direct") or agent_models.get("executor", "meta-llama/llama-3.1-70b-instruct"),
            "controller": agent_models.get("controller") or agent_models.get("executor", "meta-llama/llama-3.1-70b-instruct"),
            "finalizer": agent_models.get("finalizer") or agent_models.get("executor", "meta-llama/llama-3.1-70b-instruct"),
        },
        "variables": list(agent_cfg.get("variables", {}).keys()) if isinstance(agent_cfg.get("variables"), dict) else list(agent_cfg.get("variables", [])),
        "workflow_tool_names": sorted(workflow_tool_names),
        "knowledge_base_ids": list(agent_cfg.get("knowledge_base_ids", [])),
        "temperature": agent_cfg.get("temperature", 0.3),
        "context_window_tokens": agent_cfg.get("context_window_tokens", 4096),
        "limits": agent_cfg.get("limits") or {
            "max_iterations": 5,
            "max_tokens_per_run": 50000,
            "timeout_s": 120,
            "max_steps": 8,
            "max_parallel_steps": 3,
            "max_tool_turns": 4,
            "max_tool_output_chars": 4000,
            "max_input_chars": 30_000,
            "llm_review": True
        }
    }
    detailed_trace.record("effective_agent_configuration", config_dict)
    from app.services.agent_trace import get_trace_logger, log_startup_timing
    trace = get_trace_logger(run_id)
    log_startup_timing(trace, "request_agent_session_and_workflow_config", request_config_started)
    
    # Persist the run and incoming message before starting background work.
    run_record = Run(
        id=uuid.UUID(run_id),
        agent_id=agent.id,
        user_id=current_user.id,
        session_id=session.id,
        workflow_version_id=agent.workflow_version_id,
        end_user_id=effective_end_user_id,  # Store end_user_id
        input=input_text,
        variables=variables or {},
        status="running"
    )
    db.add(run_record)
    # Materialize the FK target before inserting the conversation row that points to it.
    await db.flush()
    db.add(ConversationMessage(
        session_id=session.id,
        run_id=uuid.UUID(run_id),
        role="user",
        content=input_text,
    ))
    # Advance the durable history cache generation atomically with the message.
    from sqlalchemy import update
    await db.flush()
    await db.execute(
        update(AgentSession)
        .where(AgentSession.id == session.id)
        .values(history_version=AgentSession.history_version + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()

    asyncio.create_task(execute_run(
        run_id=run_id,
        user_id=str(current_user.id),
        config_dict=config_dict,
        input_str=input,
        variables=variables or {},
        agent_id=str(agent.id),
        session_id=str(session.id),
        end_user_id=effective_end_user_id
    ))
    
    return {
        "run_id": run_id,
        "session_id": str(session.id),
        "status": "running",
        "message": "Run accepted. Subscribe to the authenticated run event stream for progress and the result."
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
        "workflow_version_id": str(run.workflow_version_id) if run.workflow_version_id else None,
        "status": run.status,
        "input": run.input,
        "variables": run.variables,
        "result": run.result,
        "stop_reason": run.stop_reason,
        "tokens_used": run.tokens_used,
        "duration_ms": run.duration_ms,
        "created_at": run.created_at.isoformat()
    }
    response["workflow_transitions"] = (run.state or {}).get("workflow_transitions", [])
    step_result = await db.exec(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id).order_by(WorkflowRunStep.started_at, WorkflowRunStep.id))
    response["workflow_steps"] = [
        {"id": str(step.id), "node_id": step.node_id, "attempt": step.attempt,
         "status": step.status, "input": step.input, "output": step.output,
         "error": step.error, "started_at": step.started_at.isoformat() if step.started_at else None,
         "finished_at": step.finished_at.isoformat() if step.finished_at else None}
        for step in step_result.all()
    ]
    
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

@router.get("/runs/{run_id}/stream")
async def stream_run_updates(
    run_id: str,
    current_user: User = Depends(current_user),
    db=Depends(get_db),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
):
    """Replay and stream authenticated run events from a Redis Stream."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID format")

    result = await db.exec(select(Run).where(
        Run.user_id == current_user.id,
        Run.id == run_uuid,
    ))
    run = result.first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    from app.redis_client import get_redis
    redis_client = await get_redis()
    if not redis_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSE streaming is unavailable because Redis is not configured or reachable. Use GET /v1/runs/{run_id} instead.",
        )

    channel = f"run:{run_id}"
    cursor_parts = last_event_id.split("-", 1) if last_event_id else []
    cursor = (
        last_event_id
        if len(cursor_parts) == 2 and all(part.isdigit() for part in cursor_parts)
        else "0-0"
    )
    try:
        # Redis Streams retain events emitted before the client connects.
        result = await db.exec(select(Run).where(
            Run.user_id == current_user.id,
            Run.id == run_uuid,
        ))
        run = result.first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        run_status = run.status
        run_result = run.result
        stop_reason = run.stop_reason
        run_state = run.state if isinstance(run.state, dict) else {}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Could not prepare Redis stream %s: %s", channel, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSE streaming is temporarily unavailable. Use the polling endpoint instead.",
        ) from exc

    async def event_generator():
        try:
            from app.services.redis_trace import measure_redis
            logger.info("SSE client subscribed to %s", channel)
            yield f"event: connected\ndata: {json.dumps({'run_id': run_id, 'status': run_status})}\n\n"

            steps = run_state.get("steps", [])
            progress_snapshot = {
                'status': run_status,
                'mode': run_state.get('mode'),
                'steps': [
                    {
                        'id': step.get('id'),
                        'description': str(step.get('goal', ''))[:1000],
                        'status': step.get('status', 'pending'),
                        'summary': str(step.get('result_summary') or '')[:500],
                    }
                    for step in steps if isinstance(step, dict)
                ],
            }
            yield f"event: progress_snapshot\ndata: {json.dumps(progress_snapshot)}\n\n"

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 300
            stream_cursor = cursor
            terminal = run_status in {"done", "failed", "cancelled"}
            if terminal:
                # Replay retained events for completed runs, then send a DB
                # snapshot as a fallback for older runs or Redis write errors.
                entries = await measure_redis(
                    "stream.xrange", lambda: redis_client.xrange(channel, min="-", max="+")
                )
                cursor_tuple = tuple(map(int, cursor.split("-", 1)))
                saw_terminal_event = False
                for stream_id, fields in entries:
                    if tuple(map(int, stream_id.split("-", 1))) <= cursor_tuple:
                        continue
                    try:
                        event = json.loads(fields.get("event", "{}"))
                    except (TypeError, json.JSONDecodeError):
                        logger.warning("Ignoring malformed Redis stream event on %s", channel)
                        continue
                    event_type = event.get("type", "update")
                    yield (
                        f"id: {stream_id}\nevent: {event_type}\n"
                        f"data: {json.dumps(event.get('data', {}), default=str)}\n\n"
                    )
                    stream_cursor = stream_id
                    saw_terminal_event = saw_terminal_event or event_type in {"run_completed", "run_failed"}
                if not saw_terminal_event:
                    event_type = "run_completed" if run_status == "done" else "run_failed"
                    yield f"event: {event_type}\ndata: {json.dumps({'status': run_status, 'result': run_result, 'stop_reason': stop_reason}, default=str)}\n\n"
                return

            while loop.time() < deadline:
                streams = await measure_redis(
                    "stream.xread", lambda: redis_client.xread(
                        {channel: stream_cursor}, count=100, block=1000
                    ), "block_ms=1000"
                )
                if not streams:
                    yield ": keepalive\n\n"
                    continue
                for _, entries in streams:
                    for stream_id, fields in entries:
                        stream_cursor = stream_id
                        try:
                            event = json.loads(fields.get("event", "{}"))
                        except (TypeError, json.JSONDecodeError):
                            logger.warning("Ignoring malformed Redis stream event on %s", channel)
                            continue
                        event_type = event.get("type", "update")
                        payload = event.get("data", {})
                        yield f"id: {stream_id}\nevent: {event_type}\ndata: {json.dumps(payload, default=str)}\n\n"
                        if event_type in {"run_completed", "run_failed"}:
                            return
            else:
                yield f"event: timeout\ndata: {json.dumps({'message': 'Stream timeout after 5 minutes'})}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SSE stream failed for %s", channel)
            yield f"event: error\ndata: {json.dumps({'error': 'Stream error'})}\n\n"
        finally:
            logger.info("SSE client disconnected from %s", channel)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
