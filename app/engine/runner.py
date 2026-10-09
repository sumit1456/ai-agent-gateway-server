from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from langchain_core.runnables import RunnableConfig
from app.engine.context import RUNS, RunContext
from app.engine.graph import build_graph

log = logging.getLogger(__name__)

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
    from app.models.tables import Provider, KnowledgeBase, Tool as ToolRecord
    from app.providers.registry import get_provider
    from app.security import decrypt

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

    try:
        uid = UUID(str(user_id))
    except (ValueError, TypeError):
        uid = user_id

    async with SessionLocal() as db:
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
                kbs_res = await db.exec(select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_uuids)))
                kbs = kbs_res.all()
                if kbs:
                    first_kb = kbs[0]
                    kb_namespaces = [str(k.id) for k in kbs]
                    custom_api_key = decrypt(first_kb.vector_api_key_enc) if first_kb.vector_api_key_enc else None
                    retriever = KnowledgeRetriever(
                        api_key=custom_api_key,
                        index_host=first_kb.vector_index_host,
                        rerank_model=first_kb.rerank_model
                    )

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
    ctx = RunContext(
        run_id=run_id,
        user_id=uid,
        config=config,
        persona=persona,
        llm_factory=factory,
        tools=registry,
        retriever=retriever,
        kb_namespaces=kb_namespaces,
    )

    # Stamp agent_id, session_id, and end_user_id on the context so tools can use them
    ctx.agent_id = agent_id  # type: ignore[attr-defined]
    ctx.session_id = session_id  # type: ignore[attr-defined]
    ctx.end_user_id = end_user_id  # type: ignore[attr-defined]

    # 4. Register builtin gateway tools (ctx-aware)
    from app.tools.builtin import build_builtin_tools
    for tool in build_builtin_tools(ctx):
        registry.register(tool)

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


async def run_graph(ctx: RunContext, run_id: str) -> None:
    """Run the LangGraph engine in the background."""
    import uuid
    import time
    start_time = time.time()
    error = None
    final_answer = ""
    final_stop_reason = None
    
    log.info(f"=" * 80)
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
            "last_wave": [],
            "pending_replan": False,
            "replan_reason": "",
            "plan_hashes": [],
            "stop_reason": None,
            "answer": "",
        }
        
        log.info(f"🎬 [RUN_GRAPH] Invoking graph...")
        graph_start = time.time()
        
        graph = get_graph()
        final_state = await graph.ainvoke(initial_state, config)
        
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
        from app.models.tables import Run, AgentSession
        try:
            async with SessionLocal() as db:
                run = await db.get(Run, uuid.UUID(run_id))
                if run:
                    run.status = "done" if not error else "failed"
                    run.result = {"answer": final_answer} if not error else {"error": error}
                    run.stop_reason = final_stop_reason or ("error" if error else "completed")
                    run.tokens_used = ctx.usage.total
                    run.duration_ms = int((time.time() - start_time) * 1000)
                    db.add(run)
                    
                    # Append to conversation history in session
                    if run.session_id and hasattr(ctx, 'input') and ctx.input:
                        session = await db.get(AgentSession, run.session_id)
                        if session:
                            # Initialize conversation history if None
                            if session.conversation_history is None:
                                session.conversation_history = []
                            
                            # Append user message
                            session.conversation_history.append({
                                "role": "user",
                                "content": ctx.input,
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                            
                            # Append assistant response
                            if final_answer:
                                session.conversation_history.append({
                                    "role": "assistant",
                                    "content": final_answer,
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                })
                            
                            db.add(session)
                    
                    await db.commit()
        except Exception as db_exc:
            print(f"Failed to update run record: {db_exc}")
        
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


def spawn(coro) -> asyncio.Task:
    from app.engine.context import spawn as ctx_spawn
    return ctx_spawn(coro)