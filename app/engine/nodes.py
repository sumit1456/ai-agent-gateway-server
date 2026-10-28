import asyncio, logging, time
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from app.engine import llm_utils as llm
from app.engine.prompts import get_prompt
from app.engine.retry import plan_hash
from app.engine.state_ops import (PlanError, apply_result, fallback_answer, ready_steps,
                                  validate_plan)
from app.engine.tool_loop import run_step, run_tool_loop
from app.engine.types import (InitialDecision, Plan, ReviewResult, RouteDecision,
                              RunState, StepResult, StepVerdict, StopRun)
from app.engine.views import (build_view, clip, assemble, conversation_messages,
                              context_char_budget, task_ledger)
from app.services.agent_trace import get_trace_logger

log = logging.getLogger(__name__)
MAX_STEP_RETRIES = 2          # retries per step (attempt 0 + 2 retries)

# Performance tracking helper
def _log_timing(node_name: str, start_time: float, ctx=None, extra_info: dict = None):
    """Log timing information for a node execution."""
    duration_ms = int((time.time() - start_time) * 1000)
    info = {
        "node": node_name,
        "duration_ms": duration_ms,
    }
    if ctx:
        info["run_id"] = ctx.run_id
        info["tokens_so_far"] = ctx.usage.total
    if extra_info:
        info.update(extra_info)
    
    log.info(f"⏱️  [{node_name}] completed in {duration_ms}ms | {info}")
    return duration_ms

def get_ctx(config: RunnableConfig):
    return config["configurable"]["ctx"]

# ---------------- router ----------------
async def legacy_router_node(state: RunState, config: RunnableConfig) -> dict:
    start_time = time.time()
    ctx = get_ctx(config)
    
    # Get trace logger
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("ROUTER", state)
    
    log.info(f"🚀 [ROUTER] Starting for run_id={ctx.run_id}")
    log.info(f"📝 [ROUTER] Input: {state['goal'][:100]}...")
    
    await ctx.sink.emit("run.started", {"run_id": ctx.run_id})
    from app.engine.runner import _publish_run_event
    await _publish_run_event(ctx.run_id, "node_enter", {"node": "router", "status": "Routing request"})
    visible = [n for n in ctx.tools.names() if n != "read_artifact"]
    
    log.info(f"🔧 [ROUTER] Available tools: {visible}")
    log.info(f"📚 [ROUTER] Knowledge bases: {len(ctx.kb_namespaces)} configured")
    trace.log_custom(f"Available tools: {visible}")
    trace.log_custom(f"Knowledge bases: {len(ctx.kb_namespaces)}")
    
    forced = ctx.config.limits.force_mode
    if forced:
        mode = forced
        log.info(f"⚙️  [ROUTER] Forced mode: {mode}")
        trace.log_custom(f"Using forced mode: {mode}")
    elif not visible and not ctx.kb_namespaces:
        mode = "direct"                       # nothing to plan around
        log.info(f"✅ [ROUTER] No tools/KB → direct mode")
        trace.log_custom("No tools/KB → routing to direct mode")
    else:
        llm_start = time.time()
        log.info(f"🤖 [ROUTER] Calling LLM to decide routing...")
        
        system = get_prompt(ctx.config, "router")
        user = assemble([
            ("GOAL", state["goal"]),
            ("SHARED TASK LIST", task_ledger(state)),
            ("AVAILABLE TOOLS", ", ".join(visible) if visible else "(none)"),
            ("KNOWLEDGE BASE", "available" if ctx.kb_namespaces else "not available"),
        ], context_char_budget(ctx, 5000))
        
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        
        decision: RouteDecision = await llm.structured_call(
            ctx, "reviewer", RouteDecision, system, user
        )
        
        # Log LLM interaction once here at the node level
        trace.log_llm_messages("reviewer", messages, decision)
        
        llm_duration = int((time.time() - llm_start) * 1000)
        mode = decision.mode
        log.info(f"🎯 [ROUTER] LLM decided: {mode} (reason: {decision.reason}) | LLM call took {llm_duration}ms")
        trace.log_custom(f"LLM decision: {mode} - Reason: {decision.reason}")
    
    result = {"mode": mode}
    trace.log_node_exit("ROUTER", result)
    await _publish_run_event(ctx.run_id, "node_complete", {"node": "router", "status": mode})
    trace.log_routing_decision("ROUTER", mode.upper(), f"Selected mode: {mode}")
    
    _log_timing("ROUTER", start_time, ctx, {"mode": mode, "tools_count": len(visible)})
    return result

async def router_node(state: RunState, config: RunnableConfig) -> dict:
    """Select the initial role and complete its work in one model call."""
    start_time = time.time()
    ctx = get_ctx(config)
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("ROUTER", state)
    from app.engine.runner import _publish_run_event
    await ctx.sink.emit("run.started", {"run_id": ctx.run_id})
    await _publish_run_event(ctx.run_id, "node_enter", {"node": "router", "status": "Selecting and running role"})

    prompt_started = time.perf_counter()
    visible = [name for name in ctx.tools.names() if name != "read_artifact"]
    forced = ctx.config.limits.force_mode
    system = "\n\n".join([
        get_prompt(ctx.config, "router"),
        "Choose a role and complete that role's work in this same response. For direct, provide the complete user-facing answer in answer. Choose plan when tools, knowledge retrieval, or multi-step work is needed; return executable steps and do not answer yet.",
        "For planned work, set review_required only when an independent review is materially useful; default to false for routine tasks.",
        f"Direct role instructions:\n{get_prompt(ctx.config, 'direct')}",
        f"Planner role instructions:\n{get_prompt(ctx.config, 'planner', max_steps=ctx.config.limits.max_steps)}",
    ])
    user = assemble([
        ("GOAL", state.get("goal", "")),
        ("SHARED TASK LIST", task_ledger(state)),
        ("AVAILABLE TOOLS", ", ".join(visible) if visible else "(none)"),
        ("KNOWLEDGE BASE", "available" if ctx.kb_namespaces else "not available"),
        ("REQUIRED MODE", forced or "Choose direct or plan."),
    ], context_char_budget(ctx, 9000))
    from app.services.agent_trace import log_startup_timing
    log_startup_timing(trace, "prompt_prepare.router", prompt_started)
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system), *conversation_messages(ctx, recent_limit=4), HumanMessage(content=user)]
    decision: InitialDecision = await llm.structured_call(ctx, "router", InitialDecision, system, user,
                                                          prior_messages=conversation_messages(ctx, recent_limit=4))
    trace.log_llm_messages("router", messages, decision)
    mode = forced or decision.mode
    result: dict = {"mode": mode}

    if mode == "direct":
        answer = decision.answer.strip() or "I’m sorry, I couldn’t produce an answer for that request."
        result.update({"answer": answer, "status": "done", "stop_reason": None,
                       "facts": state.get("facts", [])})
        await ctx.sink.emit("answer.done", {"text": answer})
        await _publish_run_event(ctx.run_id, "node_complete", {"node": "router", "status": "direct"})
    else:
        try:
            plan = Plan(steps=decision.steps)
            existing_ids = {step["id"] for step in state.get("steps", [])}
            new_steps = validate_plan(plan, set(ctx.tools.names()), existing_ids,
                                      {s["id"] for s in state.get("steps", []) if s["status"] == "done"},
                                      ctx.config.limits.max_steps)
        except PlanError as exc:
            trace.log_error(f"Initial plan validation failed: {exc}")
            answer = decision.answer.strip() or f"I couldn’t create a valid execution plan: {exc}"
            result.update({"mode": "direct", "answer": answer, "status": "done",
                           "stop_reason": None, "facts": state.get("facts", [])})
            mode = "direct"
            await ctx.sink.emit("answer.done", {"text": answer})
            await _publish_run_event(ctx.run_id, "node_complete", {"node": "router", "status": "direct"})
        else:
            result.update({"steps": state.get("steps", []) + new_steps,
                           "review_required": decision.review_required,
                           "plan_hashes": state.get("plan_hashes", []) + [plan_hash(new_steps)]})
            await _publish_run_event(ctx.run_id, "plan_created", {
                "steps": [{"id": step["id"], "tool": step.get("tools", []), "description": step.get("goal", "")}
                          for step in new_steps]})
            await _publish_run_event(ctx.run_id, "node_complete", {"node": "router", "status": "plan"})

    trace.log_custom(f"Selected and completed initial role: {mode} ({decision.reason})")
    trace.log_node_exit("ROUTER", result)
    trace.log_routing_decision("ROUTER", mode.upper(), decision.reason)
    _log_timing("ROUTER", start_time, ctx, {"mode": mode, "tools_count": len(visible)})
    return result

# ---------------- direct pass ----------------
async def direct_node(state: RunState, config: RunnableConfig) -> dict:
    start_time = time.time()
    ctx = get_ctx(config)
    
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("DIRECT", state)
    from app.engine.runner import _publish_run_event
    await _publish_run_event(ctx.run_id, "node_enter", {"node": "direct", "status": "Processing request"})
    
    log.info(f"💬 [DIRECT] Starting direct answer mode")
    log.info(f"📝 [DIRECT] Goal: {state['goal'][:100]}...")
    trace.log_custom(f"Goal: {state['goal']}")
    
    prompt_started = time.perf_counter()
    direct_prompt = get_prompt(ctx.config, "direct")
    system = f"{ctx.persona}\n\n{direct_prompt}"
    user = assemble([
        ("CURRENT USER MESSAGE", state["goal"]),
        ("SHARED TASK LIST", task_ledger(state)),
    ], context_char_budget(ctx, 13000))
    from app.services.agent_trace import log_startup_timing
    log_startup_timing(trace, "prompt_prepare.direct", prompt_started)
    # Combine tool names with semantic_search if a knowledge base is available
    available_tools = list(ctx.tools.names())
    if ctx.kb_namespaces:
        available_tools.append("semantic_search")
    
    log.info(f"🔧 [DIRECT] Tools available for use: {available_tools}")
    trace.log_custom(f"Available tools: {available_tools}")
    
    tool_loop_start = time.time()
    text, errors = await run_tool_loop(
        ctx, system, user, available_tools, stream_answer=True,
        prior_messages=conversation_messages(ctx, recent_limit=4),
    )
    tool_loop_duration = int((time.time() - tool_loop_start) * 1000)
    
    log.info(f"✅ [DIRECT] Tool loop completed in {tool_loop_duration}ms")
    log.info(f"📤 [DIRECT] Answer length: {len(text or '')} chars, Errors: {errors}")
    trace.log_custom(f"Tool loop completed: {tool_loop_duration}ms, answer length: {len(text or '')}, errors: {errors}")
    
    await ctx.sink.emit("answer.done", {"text": text or ""})
    await _publish_run_event(ctx.run_id, "node_complete", {"node": "direct", "status": "done"})
    
    result = {
        "status": "done",
        "answer": text or "",
        "stop_reason": None,
        "facts": state.get("facts", []),
    }
    
    trace.log_node_exit("DIRECT", result)
    trace.log_routing_decision("DIRECT", "END", "Direct answer completed")
    
    _log_timing("DIRECT", start_time, ctx, {
        "answer_length": len(text or ''),
        "tool_loop_ms": tool_loop_duration,
        "errors": errors
    })
    
    return result

# ---------------- planner ----------------
async def planner_node(state: RunState, config: RunnableConfig) -> dict:
    start_time = time.time()
    ctx = get_ctx(config)
    
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("PLANNER", state)
    from app.engine.runner import _publish_run_event
    await _publish_run_event(ctx.run_id, "node_enter", {"node": "planner", "status": "Creating execution plan"})
    
    log.info(f"📋 [PLANNER] Creating execution plan...")
    trace.log_custom(f"Existing steps: {len(state.get('steps', []))}")
    trace.log_custom(f"Replan reason: {state.get('replan_reason', 'N/A')}")
    
    system = f"{ctx.persona}\n\n{get_prompt(ctx.config, 'planner', max_steps=ctx.config.limits.max_steps)}"
    user = build_view("planner", state, ctx)
    
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    
    plan: Plan = await llm.structured_call(
        ctx, "planner", Plan, system, user
    )
    
    trace.log_llm_messages("planner", messages, plan)
    log.info(f"📋 [PLANNER] LLM generated {len(plan.steps)} new steps")
    
    try:
        existing_ids = {step["id"] for step in state.get("steps", [])}
        new_steps = validate_plan(plan, set(ctx.tools.names()), existing_ids,
                                  set(s["id"] for s in state.get("steps", []) if s["status"] == "done"),
                                  ctx.config.limits.max_steps)
    except PlanError as exc:
        # If validation fails, we fallback to direct mode to avoid losing the user's request
        log.warning(f"⚠️  [PLANNER] Plan validation failed: {exc}")
        trace.log_error(f"Plan validation failed: {exc}")
        trace.log_routing_decision("PLANNER", "DIRECT", f"Validation error: {exc}")
        return {"mode": "direct", "planner_error": str(exc)}
    
    # Initialize new steps as pending (already done in validate_plan)
    for s in new_steps:
        trace.log_custom(f"New step: {s['id']} - {s['goal'][:60]}")
    
    result = {
        "steps": state.get("steps", []) + new_steps,
        "review_required": bool(state.get("review_required") or plan.review_required),
        "pending_replan": False,
        "replan_reason": "",
        "plan_hashes": state.get("plan_hashes", []) + [plan_hash(new_steps)],
    }
    await _publish_run_event(ctx.run_id, "plan_created", {
        "steps": [{"id": step["id"], "tool": step.get("tools", []),
                   "description": step.get("goal", "")} for step in new_steps]
    })
    await _publish_run_event(ctx.run_id, "node_complete", {"node": "planner", "status": "done"})
    
    trace.log_node_exit("PLANNER", {"new_steps": len(new_steps), "total_steps": len(result["steps"])})
    trace.log_routing_decision("PLANNER", "EXECUTOR", f"Added {len(new_steps)} steps")
    
    _log_timing("PLANNER", start_time, ctx, {"new_steps": len(new_steps)})
    
    return result

# ---------------- executor ----------------
async def executor_node(state: RunState, config: RunnableConfig) -> dict:
    start_time = time.time()
    ctx = get_ctx(config)
    
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("EXECUTOR", state)
    from app.engine.runner import _publish_run_event
    await _publish_run_event(ctx.run_id, "node_enter", {"node": "executor", "status": "Executing steps"})
    
    log.info(f"⚙️  [EXECUTOR] Checking steps...")
    
    ready = ready_steps(state["steps"])
    if not ready:
        log.info(f"⚠️  [EXECUTOR] No ready steps to execute")
        trace.log_custom("No ready steps - skipping execution")
        trace.log_node_exit("EXECUTOR", {})
        return {}  # nothing to do
    
    # Safety check: if we've been iterating too long, stop
    iteration = state.get("iteration", 0)
    max_iterations = ctx.config.limits.max_iterations
    
    trace.log_limit_check("iterations", iteration, max_iterations, iteration >= max_iterations)
    
    if iteration >= max_iterations:
        log.warning(f"⚠️  [EXECUTOR] Max iterations ({max_iterations}) reached!")
        trace.log_error(f"Max iterations reached: {iteration}/{max_iterations}")
        result = {"stop_reason": "budget"}
        trace.log_node_exit("EXECUTOR", result)
        trace.log_routing_decision("EXECUTOR", "END", "Max iterations exceeded")
        return result
    
    log.info(f"⚙️  [EXECUTOR] {len(ready)} ready step(s), running up to {ctx.config.limits.max_parallel_steps}")
    trace.log_custom(f"Executing {min(len(ready), ctx.config.limits.max_parallel_steps)} steps in parallel")
    
    # Execute in parallel up to max_parallel_steps
    to_run = ready[:ctx.config.limits.max_parallel_steps]
    
    for step in to_run:
        trace.log_step_execution(step['id'], step['goal'], "starting")
        await _publish_run_event(ctx.run_id, "step_start", {
            "step_id": step["id"], "tool": step.get("tools", []),
            "description": step.get("goal", ""),
        })
    
    # Run each step
    step_results = await asyncio.gather(*[run_step(ctx, state, s) for s in to_run])
    
    # Apply results
    local_state = dict(state)  # shallow copy
    local_state["review_complete"] = False
    for step, res in zip(to_run, step_results):
        apply_result(local_state, ctx, step["id"], res)
        log.info(f"✅ [EXECUTOR] Step {step['id']}: {res.status}")
        trace.log_step_execution(step['id'], step['goal'], res.status)
        await _publish_run_event(ctx.run_id, "node_complete", {
            "node": "executor", "step_id": step["id"], "status": res.status,
            "summary": clip(res.summary, 400),
        })
    
    # Determine if we need to replan
    done_count = sum(1 for s in local_state["steps"] if s["status"] == "done")
    failed_count = sum(1 for s in local_state["steps"] if s["status"] == "failed")
    total_count = len(local_state["steps"])
    
    log.info(f"📊 [EXECUTOR] Progress: {done_count}/{total_count} done, {failed_count} failed")
    trace.log_custom(f"Progress: {done_count}/{total_count} done, {failed_count} failed")
    
    if done_count == total_count:
        # All steps done, move to review
        log.info(f"✅ [EXECUTOR] All steps complete!")
        trace.log_routing_decision("EXECUTOR", "REVIEWER", "All steps complete")
    else:
        trace.log_routing_decision("EXECUTOR", "CONTINUE", f"{total_count - done_count} steps remaining")
    
    trace.log_node_exit("EXECUTOR", {"done": done_count, "total": total_count})
    trace.log_state_snapshot(local_state, "Post-Executor State")
    
    _log_timing("EXECUTOR", start_time, ctx, {"steps_executed": len(to_run), "done": done_count, "total": total_count})
    
    return local_state

# ---------------- adaptive controller ----------------
async def controller_node(state: RunState, config: RunnableConfig) -> dict:
    """Schedule the next action from run state without spending an LLM call."""
    ctx = get_ctx(config)
    trace = get_trace_logger(ctx.run_id)
    from app.engine.runner import _publish_run_event
    trace.log_node_enter("CONTROLLER", state)

    if state.get("stop_reason") in {"timeout", "budget", "cancelled", "unrecoverable"}:
        return {"stop_reason": state["stop_reason"]}
    if reason := ctx.check_limits():
        trace.log_error(f"Controller stopped run: {reason}")
        return {"stop_reason": reason}

    steps = state.get("steps", [])
    ready = ready_steps(steps)
    terminal = bool(steps) and all(step.get("status") in {"done", "failed"} for step in steps)
    completed = [step for step in steps if step.get("status") == "done"]
    controller_iterations = state.get("controller_iterations", 0)
    max_iterations = ctx.config.limits.max_iterations

    # Execute ready work first. A reviewer retry marks its step pending, while a
    # reviewer replan leaves no ready work and sets pending_replan.
    if ready:
        action, reason = "execute", f"{len(ready)} step(s) are ready."
    elif state.get("pending_replan"):
        if controller_iterations < max_iterations:
            action, reason = "replan", state.get("replan_reason") or "Review requested a revised plan."
        else:
            action, reason = "finalize", "The replan limit was reached; presenting completed work and remaining gaps."
    elif terminal and completed and state.get("review_required") and not state.get("review_complete", False):
        action, reason = "verify", "The plan requested an independent review."
    elif terminal or not steps:
        action, reason = "finalize", "No executable steps remain."
    elif controller_iterations < max_iterations:
        action, reason = "replan", "No step is ready; revising the plan to resolve blocked dependencies."
    else:
        action, reason = "finalize", "The replan limit was reached; presenting completed work and remaining gaps."

    result = {
        "controller_action": action,
        "controller_reason": reason,
        "pending_replan": action == "replan",
        "replan_reason": reason if action == "replan" else "",
    }
    if action == "replan":
        result["controller_iterations"] = controller_iterations + 1

    trace.log_custom(f"Selected next action: {action} ({reason})")
    trace.log_node_exit("CONTROLLER", result)
    trace.log_routing_decision("CONTROLLER", action.upper(), reason)
    await _publish_run_event(ctx.run_id, "controller_decision", {
        "action": action,
        "reason": reason,
    })
    return result

# ---------------- reviewer ----------------
async def reviewer_node(state: RunState, config: RunnableConfig) -> dict:
    start_time = time.time()
    ctx = get_ctx(config)
    
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("REVIEWER", state)
    
    log.info(f"🔍 [REVIEWER] Starting review...")
    
    done_steps = [s for s in state["steps"] if s["status"] == "done"]
    if not done_steps:
        log.warning(f"⚠️  [REVIEWER] No completed steps to review!")
        trace.log_error("No completed steps to review!")
        result = {"stop_reason": "unrecoverable"}
        trace.log_node_exit("REVIEWER", result)
        trace.log_routing_decision("REVIEWER", "END", "No completed steps")
        return result
    
    # Safety check: if we've been iterating too long, force finalization
    iteration = state.get("iteration", 0)
    max_iterations = ctx.config.limits.max_iterations
    
    trace.log_limit_check("iterations", iteration, max_iterations, iteration >= max_iterations)
    trace.log_iteration_increment(iteration, iteration + 1, max_iterations)
    
    if iteration >= max_iterations:
        log.warning(f"⚠️  [REVIEWER] Max iterations ({max_iterations}) reached! Forcing finalization.")
        trace.log_error(f"Max iterations reached - forcing finalization")
        result = {
            "pending_replan": False,
            "review_complete": True,
            "stop_reason": "done",
            "answer": ""
        }
        trace.log_node_exit("REVIEWER", result)
        trace.log_routing_decision("REVIEWER", "FINALIZER", "Max iterations - forcing completion")
        return result
    
    log.info(f"🔍 [REVIEWER] Reviewing {len(done_steps)} completed step(s), iteration {iteration}/{max_iterations}")
    trace.log_custom(f"Reviewing {len(done_steps)} steps at iteration {iteration}/{max_iterations}")
    
    # Reviewer sees the same shared task ledger as planner/executor, plus the
    # full detail needed to assess completed steps.
    system = get_prompt(ctx.config, "reviewer")
    user = build_view("reviewer", state, ctx, wave=done_steps)
    
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    
    review: ReviewResult = await llm.structured_call(ctx, "reviewer", ReviewResult, system, user)
    
    trace.log_llm_messages("reviewer", messages, review)
    
    # Process the verdicts
    local_state = dict(state)  # shallow copy
    local_state["iteration"] = iteration + 1  # INCREMENT HERE
    local_state["review_complete"] = True
    needs_replan = False
    replan_reason = ""
    
    log.info(f"📋 [REVIEWER] Processing {len(review.verdicts)} verdicts")
    
    for verdict in review.verdicts:
        step = next(s for s in local_state["steps"] if s["id"] == verdict.step_id)
        log.info(f"📋 [REVIEWER] Step {verdict.step_id}: {verdict.verdict}")
        trace.log_review_verdict(verdict.step_id, verdict.verdict, verdict.reason)
        
        if verdict.verdict == "ok":
            continue
        elif verdict.verdict == "retry_step":
            # Mark step for retry
            step["status"] = "pending"
            step["problems"] = [verdict.reason] if verdict.reason else ["unspecified"]
            needs_replan = True
        elif verdict.verdict == "replan":
            needs_replan = True
            replan_reason = verdict.reason or "step impossible as written"
        elif verdict.verdict == "abort":
            log.warning(f"🛑 [REVIEWER] Aborting run: {verdict.reason}")
            trace.log_error(f"Aborting: {verdict.reason}")
            result = {"stop_reason": "unrecoverable"}
            trace.log_node_exit("REVIEWER", result)
            trace.log_routing_decision("REVIEWER", "END", f"Abort: {verdict.reason}")
            return result
    
    if needs_replan:
        log.info(f"🔄 [REVIEWER] Requesting replan: {replan_reason}")
        trace.log_custom(f"Requesting replan: {replan_reason}")
        local_state["pending_replan"] = True
        local_state["replan_reason"] = replan_reason
        trace.log_routing_decision("REVIEWER", "PLANNER", f"Replan needed: {replan_reason}")
    else:
        log.info(f"✅ [REVIEWER] All steps approved! Moving to finalize.")
        trace.log_custom("All steps approved")
        local_state["pending_replan"] = False
        local_state["stop_reason"] = "done"
        local_state["answer"] = ""  # will be filled by finalizer
        trace.log_routing_decision("REVIEWER", "FINALIZER", "All steps approved")
    
    trace.log_node_exit("REVIEWER", {"needs_replan": needs_replan, "iteration": local_state["iteration"]})
    trace.log_state_snapshot(local_state, "Post-Reviewer State")
    
    _log_timing("REVIEWER", start_time, ctx, {"verdicts": len(review.verdicts), "needs_replan": needs_replan})
    
    return local_state

# ---------------- finalize ----------------
async def finalize_node(state: RunState, config: RunnableConfig) -> dict:
    start_time = time.time()
    ctx = get_ctx(config)
    
    trace = get_trace_logger(ctx.run_id)
    trace.log_node_enter("FINALIZER", state)
    
    log.info(f"🎬 [FINALIZER] Creating final answer...")
    trace.log_custom(f"Finalizing with {len(state.get('steps', []))} steps")

    steps = state.get("steps", [])
    direct_outputs = [step for step in steps if step.get("final_output") and step.get("status") == "done"]
    if (len(direct_outputs) == 1 and all(step.get("status") == "done" for step in steps)
            and (not state.get("review_required") or state.get("review_complete"))):
        step = direct_outputs[0]
        artifact = ctx.artifacts.get(step.get("artifact_id") or "")
        answer_text = (artifact.get("content") if artifact else "") or step.get("result_summary", "")
        if answer_text:
            await ctx.sink.emit("answer.delta", {"text": answer_text})
            from app.engine.runner import _publish_run_event
            await _publish_run_event(ctx.run_id, "answer.delta", {"text": answer_text})
            await ctx.sink.emit("answer.done", {"text": answer_text})
            result = {"status": "done", "answer": answer_text,
                      "stop_reason": state.get("stop_reason"), "facts": state.get("facts", [])}
            trace.log_node_exit("FINALIZER", {"fast_path": True, "answer_length": len(answer_text)})
            trace.log_routing_decision("FINALIZER", "END", "Returned a complete executor output without another model call")
            _log_timing("FINALIZER", start_time, ctx, {"answer_length": len(answer_text), "fast_path": True})
            return result
    
    finalizer_prompt = get_prompt(ctx.config, "finalizer")
    system = f"{ctx.persona}\n\n{finalizer_prompt}"
    if state.get("controller_action") == "ask_user":
        question = state.get("controller_question") or "What detail should I use to continue?"
        system += (
            "\n\nThe workflow controller determined that essential information is missing. "
            f"Ask the user this one focused question: {question} Do not claim the task is complete."
        )
    # Build the finalizer prompt
    per_step = max(1500, 8000 // max(len(steps), 1))
    blocks = []
    for s in steps:
        if s["status"] != "done":
            blocks.append(f"[NOT DONE] {s['goal']}")
            continue
        art = ctx.artifacts.get(s.get("artifact_id") or "")
        blocks.append(f"{s['goal']}\n{clip(art['content'], per_step) if art else s.get('result_summary')}")
    stop = state.get("stop_reason") or "done"
    sections = [
        ("USER REQUEST", state["goal"]),
        ("RUN STOPPED EARLY", f"reason: {stop}" if stop not in {"done", "needs_input"} else ""),
        ("QUESTION FOR USER", state.get("controller_question", "")
         if state.get("controller_action") == "ask_user" else ""),
        ("STEP RESULTS", "\n\n".join(blocks)),
    ]
    user = assemble(sections, context_char_budget(ctx, 10000))  # finalizer budget
    
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    
    answer = await llm.llm_turn(ctx, "finalizer", messages, stream_answer=True)
    
    trace.log_llm_messages("finalizer", messages, answer)
    
    answer_text = answer.content if isinstance(answer.content, str) else str(answer.content)
    
    log.info(f"✅ [FINALIZER] Answer generated: {len(answer_text)} chars")
    trace.log_custom(f"Final answer: {len(answer_text)} chars")
    
    await ctx.sink.emit("answer.done", {"text": answer_text})
    
    result = {
        "status": "done",
        "answer": answer_text,
        "stop_reason": state.get("stop_reason"),
        "facts": state.get("facts", []),
    }
    
    trace.log_node_exit("FINALIZER", result)
    trace.log_routing_decision("FINALIZER", "END", "Final answer delivered")
    
    _log_timing("FINALIZER", start_time, ctx, {"answer_length": len(answer_text)})
    
    return result

# ---------------- helper for progress reporting ----------------
def _progress(state) -> str:
    return "\n".join(f"[{s['status']}] {s['id']}: {s['goal']} -> {s.get('result_summary') or ''}"
                     for s in state.get("steps", []))
