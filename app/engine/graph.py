from langgraph.graph import StateGraph, END
from app.engine.nodes import (router_node, direct_node, planner_node,
                              executor_node, reviewer_node, finalize_node)
from app.engine.types import RunState

def build_graph():
    """Build the LangGraph state graph."""
    workflow = StateGraph(RunState)
    
    # Add nodes
    workflow.add_node("router", router_node)
    workflow.add_node("direct", direct_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("reviewer", reviewer_node)
    workflow.add_node("finalize", finalize_node)
    
    # Set entry point
    workflow.set_entry_point("router")
    
    # Add conditional edges from router
    workflow.add_conditional_edges(
        "router",
        lambda state: state.get("mode", "direct"),
        {
            "direct": "direct",
            "plan": "planner",
        }
    )
    
    # Add edge from direct to end
    workflow.add_edge("direct", END)
    
    # Add edge from planner to executor
    workflow.add_edge("planner", "executor")
    
    # Add conditional edges from executor
    def executor_router(state):
        steps = state.get("steps", [])
        if not steps:
            # No steps planned - this shouldn't happen, but route to reviewer as safety
            return "reviewer"
        
        # Check if all steps are done
        all_done = all(s.get("status") == "done" for s in steps)
        if all_done:
            return "reviewer"
        
        # Check if there are any ready steps to execute
        pending_or_running = any(s.get("status") in ["pending", "running"] for s in steps)
        if pending_or_running:
            return "executor"  # Continue executing
        
        # All steps are either "done" or "failed" - move to reviewer
        return "reviewer"
    
    workflow.add_conditional_edges(
        "executor",
        executor_router,
        {
            "executor": "executor",
            "reviewer": "reviewer",
        }
    )
    
    # Add conditional edges from reviewer
    workflow.add_conditional_edges(
        "reviewer",
        lambda state: state.get("stop_reason") if state.get("stop_reason") else ("planner" if state.get("pending_replan") else "finalize"),
        {
            "planner": "planner",
            "finalize": "finalize",
            "done": END,
            "unrecoverable": END,
            "timeout": END,
            "budget": END,
            "cancelled": END,
        }
    )
    
    # Add edge from finalize to end
    workflow.add_edge("finalize", END)
    
    return workflow.compile()