from langgraph.graph import StateGraph, END
from app.engine.nodes import (router_node, direct_node, planner_node,
                              executor_node, controller_node, reviewer_node, finalize_node)
from app.engine.types import RunState

def build_graph():
    """Build the LangGraph state graph."""
    workflow = StateGraph(RunState)
    
    # Add nodes
    workflow.add_node("router", router_node)
    workflow.add_node("direct", direct_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("controller", controller_node)
    workflow.add_node("reviewer", reviewer_node)
    workflow.add_node("finalize", finalize_node)
    
    # Set entry point
    workflow.set_entry_point("router")

    # Add conditional edges from router
    workflow.add_conditional_edges(
        "router",
        lambda state: state.get("mode", "direct"),
        {
            "direct": END,
            "plan": "executor",
        }
    )

    # Add edge from direct to end
    workflow.add_edge("direct", END)
    
    # Add edge from planner to executor
    workflow.add_edge("planner", "executor")
    
    # Execution no longer forces a fixed reviewer/finalizer chain. The typed
    # controller selects and validates the next workflow action from run state.
    workflow.add_edge("executor", "controller")

    def controller_router(state):
        stop_reason = state.get("stop_reason")
        if stop_reason in {"timeout", "budget", "cancelled", "unrecoverable"}:
            return stop_reason
        return state.get("controller_action", "finalize")

    workflow.add_conditional_edges(
        "controller",
        controller_router,
        {
            "execute": "executor",
            "verify": "reviewer",
            "replan": "planner",
            "ask_user": "finalize",
            "finalize": "finalize",
            "timeout": END,
            "budget": END,
            "cancelled": END,
            "unrecoverable": END,
        }
    )
    
    # Review reports its findings to the controller, which chooses whether to
    # execute a retry, replan, ask the user, or finalize.
    workflow.add_edge("reviewer", "controller")
    
    # Add edge from finalize to end
    workflow.add_edge("finalize", END)

    return workflow.compile()
