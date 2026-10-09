import asyncio, logging, time
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from app.engine import llm_utils as llm
from app.engine.prompts import get_prompt
from app.engine.types import StepResult, StopRun
from app.engine.views import build_view, clip
from app.tools.call import call_tool

log = logging.getLogger(__name__)

async def run_tool_loop(ctx, system: str, user: str, tool_names: list[str],
                        stream_answer: bool = False) -> tuple[str, int]:
    """
    Mini ReAct loop: model turn -> run tool calls (in parallel) -> feed results -> repeat.
    Returns (final_text, number_of_failed_tool_calls). Raises StopRun on cancel/timeout/budget.
    The final allowed turn is made WITHOUT tools so the model must answer.
    """
    from app.engine.trace_logger import get_trace_logger
    trace = get_trace_logger(ctx.run_id)
    
    log.info(f"🔄 [TOOL_LOOP] Starting with max_turns={ctx.config.limits.max_tool_turns}")
    log.info(f"🔧 [TOOL_LOOP] Available tools: {tool_names}")
    trace.log_custom(f"Tool loop starting - max_turns={ctx.config.limits.max_tool_turns}, tools={tool_names}")
    
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    schemas = ctx.tools.schemas(tool_names)
    
    log.info(f"📋 [TOOL_LOOP] Tool schemas prepared: {len(schemas)} tools")
    
    max_turns = ctx.config.limits.max_tool_turns
    errors = 0
    
    for turn in range(max_turns + 1):
        log.info(f"🔁 [TOOL_LOOP] Turn {turn + 1}/{max_turns + 1}")
        trace.log_custom(f"Tool loop turn {turn + 1}/{max_turns + 1}")
        
        if reason := ctx.check_limits():
            log.warning(f"⚠️  [TOOL_LOOP] Limits exceeded: {reason}")
            trace.log_error(f"Limits exceeded: {reason}")
            raise StopRun(reason)
        
        last = turn == max_turns
        if last:
            log.info(f"🏁 [TOOL_LOOP] Final turn - tools disabled, forcing answer")
            trace.log_custom("Final turn - forcing answer without tools")
        
        llm_start = time.time()
        ai = await llm.llm_turn(ctx, "executor", messages,
                                tools=None if (last or not schemas) else schemas,
                                stream_answer=stream_answer)
        llm_duration = int((time.time() - llm_start) * 1000)
        
        log.info(f"🤖 [TOOL_LOOP] LLM responded in {llm_duration}ms")
        log.info(f"🛠️  [TOOL_LOOP] Tool calls requested: {len(ai.tool_calls) if ai.tool_calls else 0}")
        
        # Log the conversation turn (before tools are executed)
        trace.log_llm_messages("executor", messages, ai)
        
        if not ai.tool_calls:
            answer_length = len(ai.content if isinstance(ai.content, str) else str(ai.content))
            log.info(f"✅ [TOOL_LOOP] No tool calls - returning answer ({answer_length} chars)")
            trace.log_custom(f"Tool loop complete - answer length: {answer_length} chars, errors: {errors}")
            return (ai.content if isinstance(ai.content, str) else str(ai.content)), errors
        
        # Log each tool call
        for tc in ai.tool_calls:
            log.info(f"📞 [TOOL_LOOP] Calling tool: {tc['name']} with args: {str(tc['args'])[:100]}...")
            trace.log_tool_call(tc['name'], str(tc['args'])[:50], True, 0)  # Will update with actual result
        
        messages.append(ai)
        
        # Execute tools in parallel
        tool_exec_start = time.time()
        results = await asyncio.gather(*[call_tool(ctx, tc["name"], tc["args"]) for tc in ai.tool_calls])
        tool_exec_duration = int((time.time() - tool_exec_start) * 1000)
        
        log.info(f"⚡ [TOOL_LOOP] All tools executed in {tool_exec_duration}ms")
        trace.log_custom(f"Tools executed in {tool_exec_duration}ms")
        
        # Log results
        for tc, res in zip(ai.tool_calls, results):
            status = "✅" if res.ok else "❌"
            result_preview = str(res.output)[:100] if res.ok else str(res.error)[:100]
            log.info(f"{status} [TOOL_LOOP] {tc['name']}: {result_preview}...")
            
            # Detailed trace logging for tool results
            trace.log_tool_detail(tc['name'], tc['args'], res)
            
            errors += 0 if res.ok else 1
            messages.append(ToolMessage(content=res.model_dump_json(exclude_none=True),
                                        tool_call_id=tc["id"], name=tc["name"]))
    
    log.warning(f"⚠️  [TOOL_LOOP] Max turns reached without final answer")
    trace.log_error("Max tool turns reached without final answer")
    return "", errors

async def prefetch_kb(ctx, queries: list[str]) -> str:
    """Run the planner-declared kb_queries before a step and format the hits."""
    if not queries or not ctx.retriever or not ctx.kb_namespaces:
        return ""
    try:
        results = await asyncio.gather(*[ctx.retriever.search(ctx.kb_namespaces, q) for q in queries[:3]])
    except Exception as exc:
        log.warning("kb prefetch failed: %s", exc)
        ctx.metrics["kb_errors"] += 1
        return ""
    seen, lines = set(), []
    for chunks in results:
        for c in chunks:
            if c.id not in seen:
                seen.add(c.id)
                lines.append(f"[{c.source}] {c.text}")
    return clip("\n\n".join(lines), 3000)

async def parse_step_result(ctx, text: str) -> StepResult:
    try:
        return StepResult.model_validate(llm.extract_json(text))
    except Exception:
        ctx.metrics["bad_output_retries"] += 1
    if not text.strip():
        return StepResult(status="failed", summary="Empty response", problems=["model returned no content"])
    try:  # one cheap repair attempt
        return await llm.structured_call(ctx, "reviewer", StepResult,
                                          "Convert the text into the required JSON. Do not add information.",
                                          text, attempts=1)
    except Exception:
        return StepResult(status="done", summary=clip(text.strip(), 200), output=text)

async def run_step(ctx, state, step) -> StepResult:
    kb_text = await prefetch_kb(ctx, step.get("kb_queries", []))
    view = build_view("executor", state, ctx, step=step, kb_text=kb_text)
    names = set(step.get("tools", [])) | {"read_artifact"}
    if ctx.kb_namespaces:
        names.add("kb_search")
    executor_prompt = get_prompt(ctx.config, "executor")
    text, _errors = await run_tool_loop(ctx, f"{ctx.persona}\n\n{executor_prompt}", view, sorted(names))
    return await parse_step_result(ctx, text)