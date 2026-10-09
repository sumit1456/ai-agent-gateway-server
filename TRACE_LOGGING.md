# Trace Logging Documentation

## Overview

A comprehensive trace logging system has been implemented to capture detailed execution traces of the agent pipeline. This allows you to see exactly what the agent is doing at every step, including full LLM conversations, tool calls, state transitions, and routing decisions.

## Features

The trace logger captures:

1. **Node Execution Flow**
   - Entry and exit from each node (router, direct, planner, executor, reviewer, finalizer)
   - State snapshots at key points
   - Routing decisions between nodes

2. **LLM Conversations**
   - Full system and user prompts sent to the LLM
   - Complete LLM responses (both text and structured output)
   - Tool calls requested by the LLM
   - Parse errors and retry attempts

3. **Tool Execution**
   - Tool calls with full arguments
   - Tool execution results (success/failure)
   - Tool execution timing
   - Error details for failed tools

4. **State Tracking**
   - Complete state snapshots showing all steps, facts, artifacts
   - Iteration counters
   - Replan triggers and reasons
   - Stop reasons

5. **Performance Metrics**
   - Timing for each operation
   - Token usage tracking
   - Limit checks (iterations, tool turns, etc.)

## Output Location

Trace files are written to: `agent_traces/trace_{run_id}_{timestamp}.log`

Example: `agent_traces/trace_123e4567-e89b-12d3-a456-426614174000_20261009_154530.log`

## Trace File Format

The trace file uses a human-readable format with visual separators:

```
================================================================================
AGENT EXECUTION TRACE
Run ID: 123e4567-e89b-12d3-a456-426614174000
Started: 2026-10-09T15:45:30.123456
================================================================================

[15:45:30.123] [0.00s] 
================================================================================
>>> ENTERING NODE: ROUTER
================================================================================
    Mode: plan
    Goal: What is the capital of France?...
    Iteration: 0

[15:45:30.456] [0.33s] 💬 Available tools: [tool1, tool2, tool3]

[15:45:31.789] [1.67s] 
┌──────────────────────────────────────────────────────────────────────────────┐
│ 🤖 LLM CONVERSATION: reviewer
├──────────────────────────────────────────────────────────────────────────────┤
│ Message 1: SystemMessage
│ ────────────────────────────────────────────────────────────────────────────
│ You are a router agent...
│
├──────────────────────────────────────────────────────────────────────────────┤
│ 💬 RESPONSE:
│ ────────────────────────────────────────────────────────────────────────────
│ {"mode": "direct", "reason": "Simple question..."}
└──────────────────────────────────────────────────────────────────────────────┘

[15:45:31.800] [1.68s] <<< EXITING NODE: ROUTER
    Returned Mode: direct

[15:45:31.850] [1.73s] 
────────────────────────────────────────────────────────────────────────────────
🔀 ROUTING: ROUTER → DIRECT
   Reason: Selected mode: direct
────────────────────────────────────────────────────────────────────────────────
```

## Integration Points

The trace logger is integrated into:

1. **app/engine/nodes.py** - All 6 nodes (router, direct, planner, executor, reviewer, finalizer)
2. **app/engine/llm_utils.py** - All LLM calls (structured_call, llm_turn)
3. **app/engine/tool_loop.py** - Tool loop iterations and tool executions
4. **app/engine/runner.py** - Overall execution summary and finalization

## How It Works

1. **Initialization**: When a run starts, a `TraceLogger` instance is created with the run ID
2. **Logging**: Throughout execution, nodes and functions call trace logger methods
3. **Finalization**: When the run completes, trace logger writes a summary and closes the file
4. **Cleanup**: The trace logger is removed from memory after finalization

## Key Functions

### In trace_logger.py

- `get_trace_logger(run_id)` - Get or create trace logger for a run
- `log_node_enter(node_name, state)` - Log entering a node
- `log_node_exit(node_name, result)` - Log exiting a node
- `log_routing_decision(from_node, to_node, reason)` - Log routing between nodes
- `log_llm_messages(role, messages, response)` - Log full LLM conversation
- `log_llm_call(role, prompt_length, response_length, duration_ms)` - Log LLM call metrics
- `log_tool_call(tool_name, args, result_ok, duration_ms)` - Log tool call summary
- `log_tool_detail(tool_name, args, result)` - Log detailed tool execution
- `log_state_snapshot(state, label)` - Log complete state snapshot
- `log_step_execution(step_id, step_goal, status)` - Log step execution
- `log_review_verdict(step_id, verdict, reason)` - Log review verdicts
- `log_iteration_increment(old_iter, new_iter, max_iter)` - Log iteration changes
- `log_limit_check(limit_name, current, max_value, exceeded)` - Log limit checks
- `log_error(error_msg)` - Log errors
- `log_custom(message)` - Log custom messages
- `finalize(status, total_tokens, duration_ms)` - Write execution summary

### Usage Example

```python
from app.engine.trace_logger import get_trace_logger

# In any node or function with access to ctx.run_id
trace = get_trace_logger(ctx.run_id)

# Log entering a node
trace.log_node_enter("MY_NODE", state)

# Log custom messages
trace.log_custom("Starting some operation...")

# Log LLM conversation
from langchain_core.messages import SystemMessage, HumanMessage
messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_input)]
response = await llm.structured_call(...)
trace.log_llm_messages("my_role", messages, response)

# Log exiting the node
trace.log_node_exit("MY_NODE", result)
```

## Debugging Infinite Loops

The trace logger is especially useful for debugging infinite loops:

1. **Check iteration counters**: Look for `Iteration: X → Y` messages
2. **Review routing decisions**: See which nodes are being visited repeatedly
3. **Examine state snapshots**: Check if state is actually changing between iterations
4. **Review verdicts**: See why the reviewer is requesting replans
5. **Tool execution**: Verify tools are executing successfully
6. **LLM responses**: Check if the LLM is providing valid output

## Example Workflow

1. Run an agent:
   ```bash
   python test_agent_run.py
   ```

2. Check the trace file:
   ```bash
   ls agent_traces/
   # Output: trace_<run_id>_<timestamp>.log
   ```

3. Open the trace file and search for:
   - `ENTERING NODE` - See the execution flow
   - `LLM CONVERSATION` - See what prompts and responses
   - `ROUTING:` - See how the agent navigates
   - `Iteration:` - Track iteration progress
   - `ERROR:` - Find errors
   - `EXECUTION COMPLETE` - See final summary

## Performance Impact

The trace logger is designed to have minimal performance impact:

- Writes are buffered and flushed immediately
- File I/O is non-blocking
- Logging is fail-safe (errors in logging don't crash the run)
- Trace files are removed from memory after finalization

## Future Enhancements

Potential improvements:

1. Add structured JSON trace format option
2. Add filtering options (e.g., only log errors)
3. Add trace visualization tools
4. Add trace comparison for debugging regressions
5. Add configurable trace detail levels
6. Add trace rotation/cleanup for old files
