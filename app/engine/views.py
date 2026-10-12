from __future__ import annotations
import json
import re

# character budgets per role (~4 chars per token)
BUDGET = {"router": 5000, "direct": 13000, "planner": 6000,
          "executor": 8000, "reviewer": 5000, "finalizer": 10000}

def context_char_budget(ctx, default_chars: int) -> int | None:
    """Convert the configured token budget to an approximate character budget."""
    tokens = getattr(ctx.config, "context_window_tokens", 4096)
    if tokens is None:
        return None
    return min(400_000, max(1_000, int(default_chars * tokens / 4096)))

def clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(n - 20, 0)] + f"... [+{len(s) - n + 20} chars]"

def assemble(sections: list[tuple[str, str]], budget: int | None) -> str:
    """Add sections in priority order until the budget is used. Later sections get dropped first."""
    out, used = [], 0
    for title, body in sections:
        if not body:
            continue
        room = budget - used if budget is not None else None
        if room is not None and room < 200:
            break
        block = f"## {title}\n{clip(body, room) if room is not None else body}\n"
        out.append(block)
        used += len(block)
    return "\n".join(out)

def _facts(state) -> str:
    return "\n".join(f"- {f}" for f in state.get("facts", []))

def conversation_messages(ctx, *, recent_limit: int | None = None,
                          include_summary: bool = False,
                          include_task_state: bool = False,
                          include_retrieved_memories: bool = False) -> list:
    """Build only the conversation context explicitly requested by a caller."""
    from langchain_core.messages import AIMessage, HumanMessage

    tokens = getattr(ctx.config, "context_window_tokens", 4096)
    scale = max(1, int(tokens / 4096)) if tokens is not None else None
    def context_clip(text: str | None, default: int) -> str:
        return clip(text, default * scale) if scale is not None else (text or "")
    result = []
    task_state = getattr(ctx, "session_task_state", {}) or {}
    if include_task_state and task_state.get("status") in {"in_progress", "interrupted"}:
        unfinished = [t for t in task_state.get("tasks", [])
                      if t.get("status") not in {"done", "completed"}]
        if unfinished:
            task_lines = [
                f"- [{t.get('status', 'pending')}] {t.get('description', t.get('goal', ''))}"
                for t in unfinished
            ]
            task_context = context_clip(
                "UNFINISHED WORK FROM PRIOR RUN "
                f"({task_state.get('run_id', 'unknown')}): {task_state.get('objective', '')}\n"
                + "\n".join(task_lines), 900
            )
            result.append(HumanMessage(content=f"Session task memory (context only):\n{task_context}"))
    if include_summary and getattr(ctx, "conversation_summary", ""):
        summary = context_clip(ctx.conversation_summary, 1000)
        result.append(HumanMessage(content=f"Session summary (context only):\n{summary}"))
    messages = getattr(ctx, "recent_messages", []) or []
    if recent_limit is not None:
        messages = messages[-recent_limit:] if recent_limit > 0 else []
    if messages:
        recent = []
        used = 0
        for message in reversed(messages):
            content = message.get("content", "")
            timestamp = message.get("timestamp", "")
            rendered_size = len(content) + len(timestamp) + 24
            if scale is not None and used + rendered_size > 1800 * scale:
                room = 1800 * scale - used - 1
                if room > 200:
                    recent.append({**message, "content": clip(content, room)})
                break
            recent.append(message)
            used += rendered_size
        for message in reversed(recent):
            role = message.get("role")
            content = message.get("content", "")
            timestamp = message.get("timestamp")
            if timestamp:
                content = f"[{timestamp}]\n{content}"
            if role == "user":
                result.append(HumanMessage(content=content))
            elif role == "assistant":
                result.append(AIMessage(content=content))
    memories = getattr(ctx, "retrieved_memories", []) or [] if include_retrieved_memories else []
    if memories:
        memory_context = context_clip("\n".join(
            f"[{m.get('timestamp', 'unknown time')}] [{m.get('role', 'unknown').upper()}] "
            f"{m.get('text', '')}" for m in memories
        ), 1000)
        result.insert(0, HumanMessage(content=f"Retrieved older conversation (context only):\n{memory_context}"))
    return result

def _progress(state) -> str:
    return "\n".join(f"[{s['status']}] {s['id']}: {s['goal']} -> {s.get('result_summary') or ''}"
                     for s in state.get("steps", []))

def task_ledger(state, active_step_id: str | None = None) -> str:
    """Render the shared run checklist for every agent role."""
    steps = state.get("steps", [])
    if not steps:
        return "No planned tasks yet."
    lines = []
    for step in steps:
        status = step.get("status", "pending")
        if step.get("id") == active_step_id and status == "pending":
            status = "in_progress"
        details = [f"[{status}] {step.get('id', '?')}: {step.get('goal', '')}"]
        dependencies = step.get("depends_on", [])
        if dependencies:
            details.append(f"depends_on={', '.join(dependencies)}")
        if step.get("result_summary"):
            details.append(f"result={step['result_summary']}")
        if step.get("problems"):
            details.append(f"problems={'; '.join(step['problems'])}")
        lines.append("- " + " | ".join(details))
    return "\n".join(lines)

def _inputs(state, ctx, step) -> str:
    by_id = {s["id"]: s for s in state.get("steps", [])}
    blocks = []
    for sid in step.get("inputs", []):
        s = by_id.get(sid)
        if not s or s["status"] != "done":
            continue
        art = ctx.artifacts.get(s.get("artifact_id") or "")
        out = clip(art["content"], 1500) if art else ""
        blocks.append(f"[{sid}] {s.get('result_summary')}\n{out}\n(full output: artifact {s.get('artifact_id')})")
    return "\n\n".join(blocks)

def build_view(role: str, state, ctx, step=None, wave=None, kb_text: str | None = None) -> str:
    variables = json.dumps(state.get("variables", {}), default=str) if state.get("variables") else ""

    if role == "planner":
        tools = "\n".join(ctx.tools.describe(exclude={"read_artifact"}))
        goal_text = state.get("goal", "").lower()
        continue_intent = re.search(r"\b(continue|resume|pick up|carry on|unfinished|previous task|ongoing task)\b", goal_text)
        session_context = ""
        if continue_intent:
            task_state = getattr(ctx, "session_task_state", {}) or {}
            if task_state.get("status") in {"in_progress", "interrupted"}:
                unfinished = [task for task in task_state.get("tasks", [])
                              if task.get("status") not in {"done", "completed"}]
                session_context = "\n".join(
                    f"- [{task.get('status', 'pending')}] {task.get('description', '')}"
                    for task in unfinished
                )
            if not session_context:
                session_context = getattr(ctx, "conversation_summary", "") or ""
        sections = [
            ("GOAL", state["goal"]),
            ("RELEVANT SESSION CONTEXT", clip(session_context, 1200)),
            ("SHARED TASK LIST", task_ledger(state)),
            ("VARIABLES", variables),
            ("AVAILABLE TOOLS", tools or "(none)"),
            ("KNOWLEDGE BASE", "available (use kb_queries / semantic_search)" if ctx.kb_namespaces else "not available"),
            ("REPLAN REASON", state.get("replan_reason", "")),
            ("DEAD ENDS (do not repeat)", "\n".join(f"- {d}" for d in state.get("dead_ends", []))),
            ("FACTS", _facts(state)),
        ]
        return assemble(sections, context_char_budget(ctx, BUDGET["planner"]))

    if role == "executor":
        knowledge = kb_text if kb_text else ("no relevant results found; try semantic_search with a different query"
                                              if step.get("kb_queries") else "")
        sections = [
            ("OVERALL GOAL", state["goal"]),
            ("SHARED TASK LIST", task_ledger(state, active_step_id=step.get("id"))),
            ("YOUR STEP", f"{step['id']}: {step['goal']}"),
            ("SUCCESS CRITERIA", step.get("success_criteria", "")),
            ("PREVIOUS ATTEMPT PROBLEMS (fix these)", "\n".join(f"- {p}" for p in step.get("problems", []))),
            ("INPUTS FROM EARLIER STEPS", _inputs(state, ctx, step)),
            ("FACTS", _facts(state)),
            ("KNOWLEDGE", knowledge),
            ("VARIABLES", variables),
        ]
        return assemble(sections, context_char_budget(ctx, BUDGET["executor"]))

    if role == "reviewer":
        blocks = []
        for s in wave or []:
            art = ctx.artifacts.get(s.get("artifact_id") or "")
            blocks.append(
                f"[{s['id']}] goal: {s['goal']}\nsuccess criteria: {s.get('success_criteria', '')}\n"
                f"status: {s['status']}\nsummary: {s.get('result_summary')}\n"
                f"output: {clip(art['content'], 1500) if art else ''}\nproblems: {s.get('problems', [])}")
        return assemble([("OVERALL GOAL", state["goal"]),
                         ("SHARED TASK LIST", task_ledger(state)),
                         ("STEPS TO REVIEW", "\n\n".join(blocks))],
                        context_char_budget(ctx, BUDGET["reviewer"]))

    if role == "finalizer":
        steps = state.get("steps", [])
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
            ("SHARED TASK LIST", task_ledger(state)),
            ("RUN STOPPED EARLY", f"reason: {stop}" if stop != "done" else ""),
            ("STEP RESULTS", "\n\n".join(blocks)),
            ("FACTS", _facts(state)),
        ]
        return assemble(sections, context_char_budget(ctx, BUDGET["finalizer"]))

    raise ValueError(f"unknown role {role}")
