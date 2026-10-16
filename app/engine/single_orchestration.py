"""Run the agent's plan/execute/review cycle inside one outer graph node."""
from __future__ import annotations

from app.engine.nodes import (
    controller_node,
    direct_node,
    executor_node,
    finalize_node,
    planner_node,
    reviewer_node,
    router_node,
)


async def run_single_orchestration(initial_state: dict, config) -> dict:
    """Call the existing role logic directly, without LangGraph node transitions."""
    state = dict(initial_state)
    state.update(await router_node(state, config))
    if state.get("mode") == "direct" or state.get("stop_reason"):
        return state

    ctx = config["configurable"]["ctx"]
    max_cycles = max(1, ctx.config.limits.max_iterations + 1)
    for _ in range(max_cycles):
        if reason := ctx.check_limits():
            state["stop_reason"] = reason
            return state

        if state.get("pending_replan"):
            state.update(await planner_node(state, config))
            if state.get("mode") == "direct":
                state.update(await direct_node(state, config))
                return state

        state.update(await executor_node(state, config))
        state.update(await controller_node(state, config))

        action = state.get("controller_action", "finalize")
        if action == "verify":
            state.update(await reviewer_node(state, config))
            state.update(await controller_node(state, config))
            action = state.get("controller_action", "finalize")

        if action == "replan":
            continue
        if action in {"timeout", "budget", "cancelled", "unrecoverable"}:
            return state
        if action == "finalize" or action == "ask_user":
            state.update(await finalize_node(state, config))
            return state

    state["stop_reason"] = "budget"
    state.update(await finalize_node(state, config))
    return state
