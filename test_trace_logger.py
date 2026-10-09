#!/usr/bin/env python3
"""
Quick test of the trace logger functionality
"""
import time
from app.engine.trace_logger import TraceLogger, get_trace_logger, remove_trace_logger

def test_basic_trace():
    """Test basic trace logging functionality"""
    print("Testing TraceLogger...")
    
    # Create a trace logger
    run_id = "test-run-12345"
    trace = TraceLogger(run_id)
    
    print(f"✓ Created trace logger for run_id: {run_id}")
    print(f"✓ Trace file: {trace.trace_file}")
    
    # Test various logging methods
    trace.log_custom("Starting test execution")
    
    # Simulate node entry/exit
    state = {
        "mode": "test",
        "goal": "Test the trace logger functionality",
        "iteration": 0,
        "steps": []
    }
    
    trace.log_node_enter("TEST_NODE", state)
    trace.log_custom("Inside test node")
    
    # Simulate LLM messages
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content="You are a test assistant"),
        HumanMessage(content="What is 2+2?")
    ]
    
    class MockResponse:
        def __init__(self):
            self.content = "The answer is 4"
            self.tool_calls = []
    
    trace.log_llm_messages("test_role", messages, MockResponse())
    
    # Simulate tool call
    trace.log_tool_call("test_tool", "{'arg': 'value'}", True, 150)
    
    # Simulate tool detail
    class MockToolResult:
        def __init__(self):
            self.ok = True
            self.output = "Tool executed successfully"
    
    trace.log_tool_detail("test_tool", {"arg": "value"}, MockToolResult())
    
    # Log routing decision
    trace.log_routing_decision("TEST_NODE", "NEXT_NODE", "Test routing")
    
    # Log state snapshot
    trace.log_state_snapshot(state, "Test State")
    
    # Log limit check
    trace.log_limit_check("iterations", 5, 10, False)
    
    # Log iteration increment
    trace.log_iteration_increment(0, 1, 10)
    
    # Log review verdict
    trace.log_review_verdict("step-1", "ok", "Step completed successfully")
    
    # Log step execution
    trace.log_step_execution("step-1", "Test step goal", "done")
    
    # Log error
    trace.log_error("This is a test error (not real)")
    
    # Log node exit
    trace.log_node_exit("TEST_NODE", {"status": "done"})
    
    # Finalize
    trace.finalize("success", 1500, 2500)
    
    print(f"\n✓ All logging methods tested successfully!")
    print(f"✓ Check the trace file: {trace.trace_file}")
    
    return trace.trace_file


def test_global_registry():
    """Test the global trace logger registry"""
    print("\n\nTesting global trace logger registry...")
    
    run_id = "test-run-67890"
    
    # Get trace logger (creates new one)
    trace1 = get_trace_logger(run_id)
    print(f"✓ Got trace logger 1 for run_id: {run_id}")
    
    # Get same trace logger again (should reuse)
    trace2 = get_trace_logger(run_id)
    print(f"✓ Got trace logger 2 for run_id: {run_id}")
    
    # Should be the same instance
    assert trace1 is trace2, "Trace loggers should be the same instance!"
    print(f"✓ Confirmed both are the same instance")
    
    # Log something
    trace1.log_custom("Testing global registry")
    
    # Remove from registry
    trace_file = trace1.trace_file
    remove_trace_logger(run_id)
    print(f"✓ Removed trace logger from registry")
    print(f"✓ Trace file: {trace_file}")
    
    # Get again (should create new one)
    trace3 = get_trace_logger(run_id)
    assert trace3 is not trace1, "Should be a new instance!"
    print(f"✓ New trace logger created after removal")
    print(f"✓ New trace file: {trace3.trace_file}")
    
    # Clean up
    trace3.finalize("test_complete", 0, 0)
    remove_trace_logger(run_id)


if __name__ == "__main__":
    print("=" * 80)
    print("TRACE LOGGER TEST SUITE")
    print("=" * 80)
    
    try:
        trace_file1 = test_basic_trace()
        test_global_registry()
        
        print("\n" + "=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80)
        print("\nTrace files created:")
        print(f"  - {trace_file1}")
        print("\nYou can inspect these files to see the trace output.")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
