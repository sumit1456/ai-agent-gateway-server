from __future__ import annotations
import json

# character budgets per role (~4 chars per token)
BUDGET = {"planner": 6000, "executor": 8000, "reviewer": 5000, "finalizer": 10000}

def clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(n - 20, 0)] + f"... [+{len(s) - n + 20} chars]"

def assemble(sections: list[tuple[str, str]], budget: int) -> str:
    """Add sections in priority order until the budget is used. Later sections get dropped first."""
    out, used = [], 0
    for title, body in sections:
        if not body:
            continue
        room = budget - used
        if room < 200:
            break
        block = f"## {title}\n{clip(body, room)}\n"
        out.append(block)
        used += len(block)
    return "\n".join(out)

def _facts(state) -> str:
    return "\n".join(f"- {f}" for f in state.get("facts", []))

def _progress(state) -> str:
    return "\n".join(f"[{s['status']}] {s['id']}: {s['goal']} -> {s.get('result_summary') or ''}"
                     for s in state.get("steps", []))

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
        sections = [
            ("GOAL", state["goal"]),
            ("VARIABLES", variables),
            ("AVAILABLE TOOLS", tools or "(none)"),
            ("KNOWLEDGE BASE", "available (use kb_queries / kb_search)" if ctx.kb_namespaces else "not available"),
            ("PROGRESS SO FAR", _progress(state)),
            ("REPLAN REASON", state.get("replan_reason", "")),
            ("DEAD ENDS (do not repeat)", "\n".join(f"- {d}" for d in state.get("dead_ends", []))),
            ("FACTS", _facts(state)),
        ]
        return assemble(sections, BUDGET["planner"])

    if role == "executor":
        knowledge = kb_text if kb_text else ("no relevant results found; try kb_search with a different query"
                                              if step.get("kb_queries") else "")
        sections = [
            ("OVERALL GOAL", state["goal"]),
            ("YOUR STEP", f"{step['id']}: {step['goal']}"),
            ("SUCCESS CRITERIA", step.get("success_criteria", "")),
            ("PREVIOUS ATTEMPT PROBLEMS (fix these)", "\n".join(f"- {p}" for p in step.get("problems", []))),
            ("INPUTS FROM EARLIER STEPS", _inputs(state, ctx, step)),
            ("FACTS", _facts(state)),
            ("KNOWLEDGE", knowledge),
            ("VARIABLES", variables),
        ]
        return assemble(sections, BUDGET["executor"])

    if role == "reviewer":
        blocks = []
        for s in wave or []:
            art = ctx.artifacts.get(s.get("artifact_id") or "")
            blocks.append(
                f"[{s['id']}] goal: {s['goal']}\nsuccess criteria: {s.get('success_criteria', '')}\n"
                f"status: {s['status']}\nsummary: {s.get('result_summary')}\n"
                f"output: {clip(art['content'], 1500) if art else ''}\nproblems: {s.get('problems', [])}")
        return assemble([("OVERALL GOAL", state["goal"]), ("STEPS TO REVIEW", "\n\n".join(blocks))],
                        BUDGET["reviewer"])

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
            ("RUN STOPPED EARLY", f"reason: {stop}" if stop != "done" else ""),
            ("STEP RESULTS", "\n\n".join(blocks)),
            ("FACTS", _facts(state)),
        ]
        return assemble(sections, BUDGET["finalizer"])

    raise ValueError(f"unknown role {role}")