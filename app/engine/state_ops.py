from app.engine.types import Plan, Step, StepModel, StepResult
from app.engine.views import clip

class PlanError(Exception):
    pass

def validate_plan(plan: Plan, allowed_tools: set[str], used_ids: set[str],
                  done_ids: set[str], max_steps: int) -> list[Step]:
    """
    Turn an LLM Plan into validated Steps (as dicts).
    - new step ids must be unique and must not reuse any id in `used_ids`
    - depends_on / inputs may only reference new ids or `done_ids`
    - unknown tool names are dropped; inputs are merged into depends_on
    - dependency cycles are rejected
    
    Returns list of Step dicts (not Pydantic models) for LangGraph state.
    """
    raw = plan.steps[:max_steps]
    if not raw:
        raise PlanError("plan has no steps")
    new_ids = [s.id for s in raw]
    if len(set(new_ids)) != len(new_ids):
        raise PlanError("duplicate step ids in plan")
    clash = set(new_ids) & used_ids
    if clash:
        raise PlanError(f"step ids already used: {sorted(clash)}; use new ids")
    known = set(new_ids) | done_ids
    steps: list[Step] = []
    for s in raw:
        inputs = [i for i in dict.fromkeys(s.inputs) if i in known and i != s.id]
        deps = [d for d in dict.fromkeys(s.depends_on + inputs) if d in known and d != s.id]
        # Use Pydantic for validation, then convert to dict
        step_model = StepModel(
            id=s.id, 
            goal=s.goal, 
            tools=[t for t in s.tools if t in allowed_tools],
            inputs=inputs, 
            kb_queries=s.kb_queries[:3], 
            depends_on=deps,
            success_criteria=s.success_criteria, 
            status="pending", 
            attempts=0,
            result_summary=None, 
            artifact_id=None, 
            problems=[])
        steps.append(step_model.model_dump())
    _check_acyclic_dict(steps, set(new_ids))
    return steps

def _check_acyclic_dict(steps: list[Step], new_ids: set[str]) -> None:
    """Check for dependency cycles in dict-based steps."""
    deps = {s["id"]: {d for d in s["depends_on"] if d in new_ids} for s in steps}
    while deps:
        free = [k for k, v in deps.items() if not v]
        if not free:
            raise PlanError("plan has a dependency cycle")
        for k in free:
            deps.pop(k)
        for v in deps.values():
            v.difference_update(free)

def ready_steps(steps: list[Step]) -> list[Step]:
    done = {s["id"] for s in steps if s["status"] == "done"}
    return [s for s in steps if s["status"] == "pending" and all(d in done for d in s["depends_on"])]

def merge_facts(old: list[str], new: list[str], cap: int = 20) -> list[str]:
    out = list(old)
    seen = {f.strip().lower() for f in out}
    for f in new:
        f = clip(f.strip(), 200)
        if f and f.lower() not in seen:
            out.append(f)
            seen.add(f.lower())
    return out[-cap:]

def apply_result(local: dict, ctx, step_id: str, res: StepResult) -> None:
    """
    Record a StepResult. MUTATES `local`, a node-local dict with keys steps/facts/artifacts.
    Never pass the incoming LangGraph state here; copy first.
    """
    step = next(s for s in local["steps"] if s["id"] == step_id)
    aid = ctx.artifacts.put(res.kind or "step_output", res.output or res.summary, res.summary)
    step["status"] = res.status
    step["result_summary"] = clip(res.summary, 400)
    step["artifact_id"] = aid
    step["problems"] = res.problems
    local["facts"] = merge_facts(local["facts"], res.facts)
    local["artifacts"] = ctx.artifacts.meta()

def fallback_answer(state) -> str:
    """Used when the run stops early and an LLM call is not appropriate (budget/timeout/cancel)."""
    reason = state.get("stop_reason") or "done"
    done = [s for s in state.get("steps", []) if s["status"] == "done"]
    head = "The request could not be fully completed" + (f" ({reason})." if reason != "done" else ".")
    if not done:
        return head
    return head + "\nCompleted so far:\n" + "\n".join(f"- {s['goal']}: {s.get('result_summary', '')}" for s in done)