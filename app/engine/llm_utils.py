from __future__ import annotations
import json, logging, time
from pydantic import BaseModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from app.engine.retry import retry_async

log = logging.getLogger(__name__)

class BadOutputError(Exception):
    pass

def extract_json(text: str) -> dict:
    """Pull the first {...} object out of free text (handles markdown code fences and chatter)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise BadOutputError("no JSON object found")
    return json.loads(text[start:end + 1])

async def structured_call(ctx, role: str, schema: type[BaseModel], system: str, user: str,
                        attempts: int = 3) -> BaseModel:
    """
    Ask the model for output matching `schema`.
    1) native structured output (tool calling) -> on parse error, retry with the error appended
    2) fallback: plain JSON prompt + extract_json + validate
    Transient HTTP errors are retried inside via retry_async.
    """
    from app.engine.trace_logger import get_trace_logger
    trace = get_trace_logger(ctx.run_id)
    
    log.info(f"🎯 [LLM/{role.upper()}] Structured call for schema: {schema.__name__}")
    start_time = time.time()
    
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    log.info(f"📊 [LLM/{role.upper()}] System prompt: {len(system)} chars, User prompt: {len(user)} chars")
    trace.log_custom(f"Structured call - role={role}, schema={schema.__name__}")
    
    try:
        runnable = ctx.llm(role).with_structured_output(schema, include_raw=True)
        for attempt in range(attempts):
            llm_call_start = time.time()
            out = await retry_async(lambda: runnable.ainvoke(messages))
            llm_call_duration = int((time.time() - llm_call_start) * 1000)
            
            ctx.usage.add_message(role, out.get("raw"))
            tokens_used = ctx.usage.total
            
            log.info(f"🤖 [LLM/{role.upper()}] Attempt {attempt + 1}: {llm_call_duration}ms, Total tokens: {tokens_used}")
            trace.log_llm_call(role, len(system) + len(user), len(str(out.get("raw"))), llm_call_duration)
            
            if out.get("parsed") is not None:
                total_duration = int((time.time() - start_time) * 1000)
                log.info(f"✅ [LLM/{role.upper()}] Success! Total time: {total_duration}ms")
                # Only log conversation once - removed duplicate logging here
                return out["parsed"]
            
            err = str(out.get("parsing_error") or "empty output")
            ctx.metrics["bad_output_retries"] += 1
            log.warning(f"⚠️  [LLM/{role.upper()}] Parse error: {err[:100]}...")
            trace.log_error(f"Parse error on attempt {attempt + 1}: {err[:200]}")
            messages = messages + [HumanMessage(content=f"Your last reply did not match the schema ({err}). Reply again with valid output only.")]
    except Exception as exc:  # model without tool support, or provider rejected the schema
        log.warning(f"❌ [LLM/{role.upper()}] Structured output failed ({exc}); falling back to plain JSON")
        trace.log_error(f"Structured output failed: {exc}, falling back to plain JSON")
    
    return await _plain_json(ctx, role, schema, system, user, attempts)

async def _plain_json(ctx, role, schema, system, user, attempts):
    schema_txt = json.dumps(schema.model_json_schema())
    sys2 = f"{system}\n\nReply with ONLY a JSON object matching this JSON Schema, no other text:\n{schema_txt}"
    messages = [SystemMessage(content=sys2), HumanMessage(content=user)]
    last = ""
    for _ in range(attempts):
        msg = await retry_async(lambda: ctx.llm(role).ainvoke(messages))
        ctx.usage.add_message(role, msg)
        try:
            return schema.model_validate(extract_json(msg.content))
        except Exception as exc:
            last = str(exc)[:200]
            ctx.metrics["bad_output_retries"] += 1
            messages = messages + [AIMessage(content=msg.content),
                                   HumanMessage(content=f"Invalid JSON for the schema: {last}. Reply with the corrected JSON only.")]
    raise BadOutputError(f"could not get valid {schema.__name__}: {last}")

async def llm_turn(ctx, role: str, messages: list, tools: list | None = None,
                   stream_answer: bool = False) -> AIMessage:
    """
    One model turn (optionally with tools bound). Streams chunks; if stream_answer is True the text
    is emitted live as `answer.delta`. If the turn turns out to contain tool calls, or a retry
    happens after text was emitted, an `answer.reset` event tells the client to discard it.
    """
    from app.engine.trace_logger import get_trace_logger
    trace = get_trace_logger(ctx.run_id)
    
    log.info(f"🎯 [LLM_TURN/{role.upper()}] Starting (tools: {len(tools) if tools else 0}, stream: {stream_answer})")
    start_time = time.time()
    
    # Calculate total message length
    total_chars = sum(len(str(m.content)) for m in messages)
    log.info(f"📊 [LLM_TURN/{role.upper()}] Message history: {len(messages)} messages, {total_chars} total chars")
    trace.log_custom(f"LLM turn - role={role}, messages={len(messages)}, tools={len(tools) if tools else 0}")
    
    llm = ctx.llm(role)
    if tools:
        log.info(f"🔧 [LLM_TURN/{role.upper()}] Binding {len(tools)} tools")
        llm = llm.bind_tools(tools)
    emitted = False

    async def call():
        nonlocal emitted
        full = None
        chunk_count = 0
        async for chunk in llm.astream(messages):
            chunk_count += 1
            full = chunk if full is None else full + chunk
            if stream_answer and isinstance(chunk.content, str) and chunk.content:
                emitted = True
                await ctx.sink.emit("answer.delta", {"text": chunk.content})
        
        log.info(f"📦 [LLM_TURN/{role.upper()}] Received {chunk_count} chunks")
        
        if full is None:
            raise BadOutputError("empty model response")
        return full

    async def on_retry(n, exc):
        nonlocal emitted
        ctx.metrics["llm_transient_retries"] += 1
        log.warning(f"🔄 [LLM_TURN/{role.upper()}] Retry #{n} due to: {exc}")
        trace.log_error(f"Retry #{n}: {exc}")
        if emitted:
            await ctx.sink.emit("answer.reset", {})
            emitted = False

    msg = await retry_async(call, on_retry=on_retry)
    ctx.usage.add_message(role, msg)
    
    duration = int((time.time() - start_time) * 1000)
    tokens = ctx.usage.total
    has_tool_calls = bool(getattr(msg, "tool_calls", None))
    
    log.info(f"✅ [LLM_TURN/{role.upper()}] Completed in {duration}ms | Tokens: {tokens} | Tool calls: {len(msg.tool_calls) if has_tool_calls else 0}")
    trace.log_llm_call(role, total_chars, len(str(msg.content)), duration)
    
    # Don't log full conversation here - it's logged at the node level to avoid duplication
    
    if stream_answer and emitted and has_tool_calls:
        log.info(f"🔄 [LLM_TURN/{role.upper()}] Resetting answer due to tool calls")
        await ctx.sink.emit("answer.reset", {})
    
    return msg