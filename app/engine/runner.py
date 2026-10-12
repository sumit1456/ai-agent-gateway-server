from __future__ import annotations
import asyncio
import logging
from langchain_core.runnables import RunnableConfig
from app.engine.context import RUNS, RunContext
from app.engine.graph import build_graph

log = logging.getLogger(__name__)


async def _publish_run_event(run_id: str, event_type: str, data: dict) -> None:
    """Publish progress without allowing Redis failures to affect a run."""
    try:
        from app.redis_client import publish_event
        await publish_event(f"run:{run_id}", event_type, data)
    except Exception as exc:
        log.warning("Failed to publish event for run %s: %s", run_id, exc)

_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def build_context(run_id: str, user_id: str, config_dict: dict,
                        input_str: str, variables: dict | None = None,
                        agent_id: str | None = None,
                        session_id: str | None = None,
                        end_user_id: str | None = None) -> RunContext:
    """Build a RunContext for a new run."""
    from uuid import UUID
    from jinja2.sandbox import SandboxedEnvironment
    from app.models.agent_config import AgentConfig
    from app.tools.registry import ToolRegistry
    from app.knowledge.retriever import KnowledgeRetriever
    from app.db import SessionLocal
    from sqlmodel import select
    from app.models.tables import Provider, KnowledgeBase, Tool as ToolRecord, AgentSession, ConversationMessage, Run
    from app.providers.registry import get_provider
    from app.security import decrypt
    from sqlalchemy import or_

    config = AgentConfig(**config_dict)
    vars_dict = variables or {}
    try:
        persona = SandboxedEnvironment().from_string(config.system_prompt).render(**vars_dict)
    except Exception:
        persona = config.system_prompt

    provider_obj = None
    decrypted_api_key = ""
    retriever = None
    kb_namespaces = []
    user_tools: list = []
    conversation_summary = ""
    recent_messages: list[dict] = []
    history_snapshot: list[dict] = []
    session_task_state: dict = {}
    history_truncated = False
    retrieved_memories: list[dict] = []

    try:
        uid = UUID(str(user_id))
    except (ValueError, TypeError):
        uid = user_id

    async with SessionLocal() as db:
        # Load prior complete session context before starting any model calls.
        if session_id:
            try:
                session_uuid = UUID(str(session_id))
                session = await db.get(AgentSession, session_uuid)
                if session:
                    conversation_summary = session.summary or ""
                    session_task_state = session.task_state or {}
                    msg_res = await db.exec(
                        select(ConversationMessage)
                        .where(ConversationMessage.session_id == session_uuid)
                        .outerjoin(Run, ConversationMessage.run_id == Run.id)
                        .where(or_(ConversationMessage.run_id.is_(None), Run.status == "done"))
                        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
                        .limit(100)
                    )
                    prior_rows = msg_res.all()
                    by_run: dict = {}
                    legacy_rows = []
                    for message in prior_rows:
                        if message.run_id is None:
                            legacy_rows.append(message)
                        else:
                            by_run.setdefault(message.run_id, []).append(message)
                    complete_runs = [
                        rows for rows in by_run.values()
                        if {message.role for message in rows} >= {"user", "assistant"}
                    ][:5]
                    selected_rows = [message for rows in complete_runs for message in rows]
                    if len(complete_runs) < 3:
                        selected_rows.extend(legacy_rows[: 6 * (3 - len(complete_runs))])
                    selected_rows.sort(key=lambda message: (message.created_at, str(message.id)))
                    recent_messages = [
                        {"role": m.role, "content": m.content, "timestamp": m.created_at.isoformat()}
                        for m in selected_rows
                    ]
                    history_snapshot = recent_messages
                    # Bound raw transcript context by characters (~4 chars/token).
                    # Keep the newest messages first, then restore chronological order.
                    context_budget = 4000
                    bounded = []
                    used_chars = 0
                    for message in reversed(recent_messages):
                        rendered_size = len(message["content"]) + len(message["timestamp"]) + 24
                        if used_chars + rendered_size > context_budget:
                            history_truncated = True
                            remaining = context_budget - used_chars - len(message["timestamp"]) - 24
                            if remaining > 200:
                                bounded.append({**message, "content": message["content"][-remaining:]})
                            break
                        bounded.append(message)
                        used_chars += rendered_size
                    recent_messages = list(reversed(bounded))
            except (ValueError, TypeError):
                log.warning("Could not load conversation context for run %s", run_id)

        # 1. Resolve Provider
        p_res = None
        try:
            p_uuid = UUID(str(config.provider_id))
            p_res = await db.exec(select(Provider).where(Provider.user_id == uid, Provider.id == p_uuid))
        except (ValueError, TypeError):
            pass
        if not p_res:
            p_res = await db.exec(select(Provider).where(Provider.user_id == uid, Provider.provider == str(config.provider_id)))
        provider_record = p_res.first() if p_res else None
        if provider_record:
            try:
                provider_obj = get_provider(provider_record.provider)
                decrypted_api_key = decrypt(provider_record.api_key_enc)
                print(f"✓ Resolved provider: {provider_record.provider} (ID: {provider_record.id})")
                print(f"✓ API key loaded: {decrypted_api_key[:8]}..." if decrypted_api_key else "✗ No API key")
            except Exception as e:
                print(f"Error initializing provider {provider_record.provider}: {e}")
        else:
            print(f"✗ Provider not found for config.provider_id={config.provider_id}, user_id={uid}")

        # 2. Resolve attached Knowledge Bases if any
        if config.knowledge_base_ids:
            kb_uuids = []
            for kbid in config.knowledge_base_ids:
                try:
                    kb_uuids.append(UUID(str(kbid)))
                except (ValueError, TypeError):
                    pass
            if kb_uuids:
                kbs_res = await db.exec(select(KnowledgeBase).where(
                    KnowledgeBase.user_id == uid,
                    KnowledgeBase.id.in_(kb_uuids),
                ))
                kbs = kbs_res.all()
                if kbs:
                    first_kb = kbs[0]
                    kb_namespaces = [k.namespace for k in kbs]
                    custom_api_key = decrypt(first_kb.vector_api_key_enc) if first_kb.vector_api_key_enc else None
                    retriever = KnowledgeRetriever(
                        api_key=custom_api_key,
                        index_host=first_kb.vector_index_host,
                        rerank_model=first_kb.rerank_model
                    )

        # When the raw recent transcript had to be clipped, supplement it with a
        # couple of older semantically relevant messages instead of expanding the prompt.
        if history_truncated and retriever and session_id and agent_id:
            try:
                memory_namespace = f"agent-{agent_id}-conversation"
                memory_matches = await retriever.search(
                    namespaces=[memory_namespace],
                    query=input_str,
                    top_k=5,
                    final_k=2,
                    metadata_filter={
                        "$and": [
                            {"record_type": {"$eq": "conversation_message"}},
                            {"session_id": {"$eq": str(session_id)}},
                            {"user_id": {"$eq": str(uid)}},
                            {"agent_id": {"$eq": str(agent_id)}},
                            {"run_id": {"$ne": str(run_id)}},
                        ]
                    },
                )
                retrieved_memories = [
                    {"text": item.get("text", ""), **item.get("metadata", {})}
                    for item in memory_matches
                ]
            except Exception as exc:
                log.info("Session semantic retrieval skipped for run %s: %s", run_id, exc)

        # 3. Load user-defined tools from DB
        if config.tools:
            tools_res = await db.exec(
                select(ToolRecord).where(
                    ToolRecord.user_id == uid,
                    ToolRecord.name.in_(config.tools),
                )
            )
            user_tools = list(tools_res.all())

    temps = {"planner": 0.2, "reviewer": 0.0}
    def factory(role: str):
        if not provider_obj or not decrypted_api_key:
            error_msg = f"Cannot create LLM for role '{role}': "
            if not provider_obj:
                error_msg += "Provider not configured"
            elif not decrypted_api_key:
                error_msg += "API key not available"
            print(f"✗ {error_msg}")
            raise ValueError(error_msg)
        model_name = getattr(config.models, role, None) or config.models.executor
        print(f"✓ Creating {role} LLM: {model_name} using {provider_obj.provider_id}")
        return provider_obj.get_chat_model(
            model=model_name,
            api_key=decrypted_api_key,
            temperature=temps.get(role, config.temperature)
        )

    registry = ToolRegistry()
    conversation_namespace = (
        f"agent-{agent_id}-conversation" if retriever and agent_id and session_id else None
    )
    ctx = RunContext(
        run_id=run_id,
        user_id=uid,
        config=config,
        persona=persona,
        llm_factory=factory,
        tools=registry,
        retriever=retriever,
        kb_namespaces=kb_namespaces,
        conversation_summary=conversation_summary,
        recent_messages=recent_messages,
        session_task_state=session_task_state,
        retrieved_memories=retrieved_memories,
        session_id=session_id,
        agent_id=agent_id,
        end_user_id=end_user_id,
        conversation_namespace=conversation_namespace,
    )

    # 4. Register builtin gateway tools (ctx-aware)
    from app.tools.builtin import build_builtin_tools
    for tool in build_builtin_tools(ctx):
        registry.register(tool)

    if session_id and history_snapshot:
        from app.redis_client import prime_recent_conversation_history
        await prime_recent_conversation_history(str(session_id), history_snapshot)

    # 5. Register user-defined tools
    if user_tools:
        _register_user_tools(registry, user_tools)

    RUNS[run_id] = ctx
    return ctx


def _register_user_tools(registry, tool_records) -> None:
    """Wrap DB tool records into BaseTool instances and register them."""
    from app.tools.webhook import WebhookTool
    from app.tools.client import ClientTool
    from app.security import decrypt
    for rec in tool_records:
        try:
            if rec.kind == "webhook":
                auth = decrypt(rec.auth_enc) if rec.auth_enc else None
                registry.register(WebhookTool(
                    name=rec.name,
                    description=rec.description,
                    endpoint_url=rec.endpoint_url,
                    auth_enc=auth,
                    timeout_ms=rec.timeout_ms,
                ))
            elif rec.kind == "client":
                registry.register(ClientTool(name=rec.name, description=rec.description))
        except Exception as exc:
            print(f"Warning: could not load tool {rec.name!r}: {exc}")


async def _compact_session_history(ctx, run_id: str) -> None:
    """Summarize older turns in batches; keep the newest three turns verbatim."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.engine.retry import retry_async
    from app.db import SessionLocal
    from app.models.tables import AgentSession, ConversationMessage, Run
    from sqlmodel import select
    from sqlalchemy import func, or_
    from uuid import UUID

    try:
        async with SessionLocal() as db:
            run = await db.get(Run, UUID(run_id))
            if not run or not run.session_id:
                return
            session = await db.get(AgentSession, run.session_id)
            if not session:
                return
            summarized = session.summary_message_count or 0
            count_result = await db.exec(
                select(func.count(ConversationMessage.id))
                .outerjoin(Run, ConversationMessage.run_id == Run.id)
                .where(ConversationMessage.session_id == session.id,
                       or_(ConversationMessage.run_id.is_(None), Run.status == "done"))
            )
            older_count = max(0, count_result.one() - 6)
            if older_count < summarized + 12:
                return
            batch_result = await db.exec(
                select(ConversationMessage)
                .outerjoin(Run, ConversationMessage.run_id == Run.id)
                .where(ConversationMessage.session_id == session.id,
                       or_(ConversationMessage.run_id.is_(None), Run.status == "done"))
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
                .offset(summarized)
                .limit(min(24, older_count - summarized))
            )
            batch = batch_result.all()
            previous_summary = session.summary or "(none)"

        history = "\n".join(
            f"[{m.created_at.isoformat()}] {m.role.upper()}: {m.content[:1500]}"
            for m in batch
        )
        response = await retry_async(lambda: ctx.llm("reviewer").ainvoke([
            SystemMessage(content=(
                "Update a concise memory summary of a user-assistant conversation. "
                "Preserve durable user preferences, facts, ongoing tasks, and unresolved questions. "
                "Drop transient details and do not invent facts. Return only the updated summary."
            )),
            HumanMessage(content=f"EXISTING SUMMARY:\n{previous_summary}\n\nNEW OLDER MESSAGES:\n{history}"),
        ]))
        summary = response.content if isinstance(response.content, str) else str(response.content)
        summary = summary.strip()[:6000]
        ctx.usage.add_message("reviewer", response)

        async with SessionLocal() as db:
            session = await db.get(AgentSession, run.session_id)
            run_record = await db.get(Run, UUID(run_id))
            if session and run_record:
                session.summary = summary.strip()
                session.summary_message_count = summarized + len(batch)
                run_record.tokens_used = ctx.usage.total
                db.add(session)
                db.add(run_record)
                await db.commit()
    except Exception as exc:
        log.warning("Session history compaction failed for run %s: %s", run_id, exc)


async def run_graph(ctx: RunContext, run_id: str) -> None:
    """Run the LangGraph engine in the background."""
    import uuid
    import time
    start_time = time.time()
    error = None
    final_answer = ""
    final_stop_reason = None
    final_state = {}
    
    log.info(f"=" * 80)
    await _publish_run_event(run_id, "run_started", {
        "input": getattr(ctx, "input", ""),
        "agent_id": getattr(ctx, "agent_id", None),
        "workflow_version_id": getattr(ctx.config, "workflow_version_id", None),
    })
    log.info(f"🚀 [RUN_GRAPH] Starting run_id={run_id}")
    log.info(f"📝 [RUN_GRAPH] Input: {ctx.input if hasattr(ctx, 'input') else 'N/A'}")
    log.info(f"👤 [RUN_GRAPH] User: {ctx.user_id}")
    log.info(f"=" * 80)
    
    try:
        config: RunnableConfig = {"configurable": {"ctx": ctx}}
        from app.engine.types import RunState
        initial_state: RunState = {
            "run_id": run_id,
            "input": ctx.input if hasattr(ctx, 'input') else "",
            "variables": ctx.config.variables or {},
            "mode": "plan",
            "goal": ctx.input if hasattr(ctx, 'input') else "",
            "steps": [],
            "facts": [],
            "artifacts": {},
            "dead_ends": [],
            "iteration": 0,
            "controller_iterations": 0,
            "review_complete": False,
            "last_wave": [],
            "pending_replan": False,
            "replan_reason": "",
            "plan_hashes": [],
            "stop_reason": None,
            "answer": "",
        }
        
        log.info(f"🎬 [RUN_GRAPH] Invoking graph...")
        graph_start = time.time()
        
        final_state = dict(initial_state)
        workflow_definition = getattr(ctx.config, "workflow_definition", None)
        if workflow_definition:
            from app.engine.workflow_runner import run_workflow
            final_state.update(await run_workflow(ctx, run_id, workflow_definition))
            await _persist_run_checkpoint(run_id, ctx, final_state)
        else:
            graph = get_graph()
            async for update in graph.astream(initial_state, config, stream_mode="updates"):
                for node_update in update.values():
                    if isinstance(node_update, dict):
                        final_state.update(node_update)
                        await _persist_run_checkpoint(run_id, ctx, final_state)
        
        graph_duration = int((time.time() - graph_start) * 1000)
        log.info(f"✅ [RUN_GRAPH] Graph completed in {graph_duration}ms")
        
        # Extract final answer and stop reason from final state
        if final_state:
            final_answer = final_state.get("answer", "")
            final_stop_reason = final_state.get("stop_reason")
            log.info(f"📤 [RUN_GRAPH] Final answer length: {len(final_answer)} chars")
            log.info(f"🏁 [RUN_GRAPH] Stop reason: {final_stop_reason}")
            
    except Exception as exc:
        import traceback
        error_detail = f"{exc}\n\n{traceback.format_exc()}"
        log.error(f"❌ [RUN_GRAPH] Error: {error_detail}")
        ctx.metrics["engine_errors"] += 1
        error = str(exc)
        await ctx.sink.emit("run.error", {"error": error_detail})
        await _publish_run_event(run_id, "run_failed", {
            "error": error,
            "error_type": type(exc).__name__,
        })
    finally:
        # Persist artifacts to Postgres before closing
        await ctx.artifacts.persist(run_id)
        
        total_duration = int((time.time() - start_time) * 1000)
        total_tokens = ctx.usage.total
        
        log.info(f"=" * 80)
        log.info(f"🏁 [RUN_GRAPH] EXECUTION SUMMARY for run_id={run_id}")
        log.info(f"⏱️  Total Duration: {total_duration}ms ({total_duration/1000:.2f}s)")
        log.info(f"🎯 Status: {'✅ SUCCESS' if not error else '❌ FAILED'}")
        log.info(f"💬 Answer Length: {len(final_answer)} chars")
        log.info(f"🪙  Total Tokens: {total_tokens}")
        log.info(f"📊 Token Breakdown: {dict(ctx.usage.by_role)}")
        log.info(f"📈 Metrics: {dict(ctx.metrics)}")
        log.info(f"=" * 80)
        
        # Finalize trace logging
        from app.engine.trace_logger import get_trace_logger, remove_trace_logger
        try:
            trace = get_trace_logger(run_id)
            trace.finalize(
                status="success" if not error else "failed",
                total_tokens=total_tokens,
                duration_ms=total_duration
            )
            remove_trace_logger(run_id)
        except Exception as trace_exc:
            log.warning(f"Failed to finalize trace: {trace_exc}")
        
        # Update Run record in DB
        from app.db import SessionLocal
        from app.models.tables import Run, ConversationMessage
        run = None
        try:
            async with SessionLocal() as db:
                run = await db.get(Run, uuid.UUID(run_id))
                if run:
                    run.status = "done" if not error else "failed"
                    run.result = {"answer": final_answer} if not error else {"error": error}
                    run.stop_reason = final_stop_reason or ("error" if error else "completed")
                    run.tokens_used = ctx.usage.total
                    run.duration_ms = int((time.time() - start_time) * 1000)
                    if final_state:
                        run.state = final_state
                    db.add(run)
                    
                    if run.session_id and final_answer and not error:
                        db.add(ConversationMessage(
                            session_id=run.session_id,
                            run_id=run.id,
                            role="assistant",
                            content=final_answer,
                        ))

                    if run.session_id and final_state.get("steps"):
                        from app.models.tables import AgentSession
                        session = await db.get(AgentSession, run.session_id)
                        if session:
                            session.task_state = _task_state_snapshot(
                                final_state, run_id, "interrupted" if error else "completed"
                            )
                            db.add(session)
                    
                    await db.commit()
        except Exception as db_exc:
            print(f"Failed to update run record: {db_exc}")

        if run and run.session_id and final_answer and not error:
            from app.redis_client import invalidate_conversation_history
            await invalidate_conversation_history(str(run.session_id))

        if not error and final_answer:
            await _index_run_conversation(ctx, run_id)
            await _compact_session_history(ctx, run_id)
            total_tokens = ctx.usage.total

        if not error:
            await _publish_run_event(run_id, "run_completed", {
                "output": final_answer,
                "tokens_used": total_tokens,
                "duration_ms": total_duration,
                "stop_reason": final_stop_reason,
            })
        
        await ctx.sink.emit("run.done", {"run_id": run_id})


async def execute_run(run_id: str, user_id: str, config_dict: dict,
                      input_str: str, variables: dict | None = None,
                      agent_id: str | None = None,
                      session_id: str | None = None,
                      end_user_id: str | None = None) -> None:
    """Entry point for starting a run."""
    ctx = await build_context(run_id, user_id, config_dict, input_str, variables, 
                             agent_id=agent_id, session_id=session_id, end_user_id=end_user_id)
    ctx.input = input_str       # type: ignore[attr-defined]
    ctx.variables = variables or {}  # type: ignore[attr-defined]
    spawn(run_graph(ctx, run_id))


def _task_state_snapshot(state: dict, run_id: str, status: str) -> dict:
    """Small session-level task ledger; Run.state remains the full run checkpoint."""
    return {
        "run_id": run_id,
        "objective": state.get("goal", state.get("input", "")),
        "status": status,
        "tasks": [
            {
                "id": step.get("id"),
                "description": step.get("goal", ""),
                "status": step.get("status", "pending"),
                "summary": step.get("result_summary") or "",
            }
            for step in state.get("steps", [])
        ],
    }


async def _persist_run_checkpoint(run_id: str, ctx: RunContext, state: dict) -> None:
    """Persist graph state after each completed node for interruption recovery."""
    from uuid import UUID
    from app.db import SessionLocal
    from app.models.tables import Run, AgentSession

    try:
        async with SessionLocal() as db:
            run = await db.get(Run, UUID(run_id))
            if not run:
                return
            run.state = state
            db.add(run)
            if run.session_id and state.get("steps"):
                session = await db.get(AgentSession, run.session_id)
                if session:
                    session.task_state = _task_state_snapshot(state, run_id, "in_progress")
                    db.add(session)
            await db.commit()
    except Exception as exc:
        log.warning("Could not persist checkpoint for run %s: %s", run_id, exc)


async def _index_run_conversation(ctx: RunContext, run_id: str) -> None:
    """Upsert the completed SQL transcript to Pinecone with stable IDs and session filters."""
    if not ctx.retriever or not ctx.conversation_namespace or not ctx.session_id:
        return
    from uuid import UUID
    from app.db import SessionLocal
    from app.models.tables import ConversationMessage, Run
    from sqlmodel import select

    try:
        async with SessionLocal() as db:
            run = await db.get(Run, UUID(run_id))
            if not run or run.status != "done":
                return
            result = await db.exec(
                select(ConversationMessage)
                .where(ConversationMessage.run_id == run.id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )
            messages = result.all()
        if not messages:
            return
        docs = [
            {
                "id": str(message.id),
                "text": message.content[:6000],
                "source": "conversation",
                "metadata": {
                    "record_type": "conversation_message",
                    "session_id": str(ctx.session_id),
                    "agent_id": str(ctx.agent_id or ""),
                    "user_id": str(ctx.user_id),
                    "end_user_id": str(ctx.end_user_id or "default"),
                    "run_id": str(run_id),
                    "message_id": str(message.id),
                    "role": message.role,
                    "timestamp": message.created_at.isoformat(),
                },
            }
            for message in messages
        ]
        await ctx.retriever.add_documents(ctx.conversation_namespace, docs)
    except Exception as exc:
        log.warning("Conversation vector indexing failed for run %s: %s", run_id, exc)


def spawn(coro) -> asyncio.Task:
    from app.engine.context import spawn as ctx_spawn
    return ctx_spawn(coro)
