"""
Trace Logger - Detailed execution trace for debugging agent pipeline

Writes a human-readable trace file showing exactly what the agent is doing.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

class TraceLogger:
    """Logs detailed trace of agent execution to a file."""
    
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.start_time = time.time()
        
        # Create logs directory if it doesn't exist
        log_dir = Path("agent_traces")
        log_dir.mkdir(exist_ok=True)
        
        # Create trace file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.trace_file = log_dir / f"trace_{run_id}_{timestamp}.log"
        
        # Initialize file with header
        with open(self.trace_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"AGENT EXECUTION TRACE\n")
            f.write(f"Run ID: {run_id}\n")
            f.write(f"Started: {datetime.now().isoformat()}\n")
            f.write("=" * 80 + "\n\n")
    
    def _elapsed(self) -> str:
        """Get elapsed time since start."""
        elapsed = time.time() - self.start_time
        return f"{elapsed:.2f}s"
    
    def _write(self, message: str):
        """Write message to trace file."""
        try:
            with open(self.trace_file, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                f.write(f"[{timestamp}] [{self._elapsed()}] {message}\n")
                f.flush()  # Ensure immediate write
        except Exception as e:
            print(f"Failed to write trace: {e}")
    
    def log_node_enter(self, node_name: str, state: Dict[str, Any]):
        """Log entry into a node."""
        self._write("\n" + "=" * 80)
        self._write(f">>> ENTERING NODE: {node_name}")
        self._write("=" * 80)
        
        # Log relevant state info
        if "mode" in state:
            self._write(f"    Mode: {state['mode']}")
        if "goal" in state:
            self._write(f"    Goal: {state['goal'][:100]}...")
        if "iteration" in state:
            self._write(f"    Iteration: {state.get('iteration', 0)}")
        if "steps" in state:
            steps = state["steps"]
            self._write(f"    Total Steps: {len(steps)}")
            if steps:
                statuses = {}
                for s in steps:
                    status = s.get("status", "unknown")
                    statuses[status] = statuses.get(status, 0) + 1
                self._write(f"    Step Statuses: {statuses}")
        
    def log_node_exit(self, node_name: str, result: Dict[str, Any]):
        """Log exit from a node."""
        self._write(f"<<< EXITING NODE: {node_name}")
        
        # Log what changed
        if "mode" in result:
            self._write(f"    Returned Mode: {result['mode']}")
        if "stop_reason" in result:
            self._write(f"    Stop Reason: {result['stop_reason']}")
        if "pending_replan" in result:
            self._write(f"    Pending Replan: {result['pending_replan']}")
        if "steps" in result:
            self._write(f"    Steps Count: {len(result['steps'])}")
        
        self._write("")  # Blank line
    
    def log_routing_decision(self, from_node: str, to_node: str, reason: str = ""):
        """Log a routing decision."""
        self._write("─" * 80)
        self._write(f"🔀 ROUTING: {from_node} → {to_node}")
        if reason:
            self._write(f"   Reason: {reason}")
        self._write("─" * 80)
    
    def log_llm_call(self, role: str, prompt_length: int, response_length: int, duration_ms: int):
        """Log an LLM call."""
        self._write(f"🤖 LLM Call: role={role}, prompt={prompt_length} chars, response={response_length} chars, duration={duration_ms}ms")
    
    def log_llm_messages(self, role: str, messages: list, response: Any = None):
        """Log full LLM conversation - prompts and response."""
        self._write("\n" + "┌" + "─" * 78 + "┐")
        self._write(f"│ 🤖 LLM CONVERSATION: {role}")
        self._write("├" + "─" * 78 + "┤")
        
        # Log each message (condensed format)
        for i, msg in enumerate(messages, 1):
            role_name = getattr(msg, '__class__', type(msg)).__name__
            content = str(getattr(msg, 'content', msg))
            
            self._write(f"│ Message {i}: {role_name}")
            self._write("│ " + "─" * 76)
            
            # Truncate long content - only show first 10 lines
            lines = content.split('\n')
            max_lines = 10
            for line in lines[:max_lines]:
                # Truncate long lines instead of wrapping
                if len(line) > 76:
                    self._write(f"│ {line[:73]}...")
                else:
                    self._write(f"│ {line}")
            
            if len(lines) > max_lines:
                self._write(f"│ ... ({len(lines) - max_lines} more lines)")
            self._write("│")
        
        # Log response if provided
        if response:
            self._write("├" + "─" * 78 + "┤")
            self._write("│ 💬 RESPONSE:")
            self._write("│ " + "─" * 76)
            
            # Get response content
            resp_content = ""
            if hasattr(response, 'content'):
                resp_content = str(response.content)
            else:
                resp_content = str(response)
            
            # Check for tool calls
            if hasattr(response, 'tool_calls') and response.tool_calls:
                self._write(f"│ 🔧 Tool Calls: {len(response.tool_calls)}")
                for tc in response.tool_calls:
                    self._write(f"│   - {tc.get('name', 'unknown')}({str(tc.get('args', {}))[:50]}...)")
                self._write("│")
            
            # Log response content (condensed)
            resp_lines = resp_content.split('\n')
            max_lines = 10
            for line in resp_lines[:max_lines]:
                if len(line) > 76:
                    self._write(f"│ {line[:73]}...")
                else:
                    self._write(f"│ {line}")
            
            if len(resp_lines) > max_lines:
                self._write(f"│ ... ({len(resp_lines) - max_lines} more lines)")
        
        self._write("└" + "─" * 78 + "┘")
        self._write("")
    
    def log_state_snapshot(self, state: Dict[str, Any], label: str = "State"):
        """Log complete state snapshot."""
        self._write("\n" + "╔" + "═" * 78 + "╗")
        self._write(f"║ 📸 {label.upper()}")
        self._write("╠" + "═" * 78 + "╣")
        
        # Key state fields
        for key in ['mode', 'goal', 'iteration', 'stop_reason', 'pending_replan', 'replan_reason']:
            if key in state:
                value = state[key]
                if key == 'goal' and isinstance(value, str) and len(value) > 60:
                    value = value[:60] + "..."
                self._write(f"║ {key}: {value}")
        
        # Steps detail
        if 'steps' in state and state['steps']:
            steps = state['steps']
            self._write(f"║ steps: {len(steps)} total")
            
            status_counts = {}
            for s in steps:
                status = s.get('status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1
            
            self._write(f"║   Status breakdown: {status_counts}")
            
            # Show each step
            for s in steps:
                step_id = s.get('id', '?')
                status = s.get('status', '?')
                goal = s.get('goal', 'no goal')[:50]
                self._write(f"║   [{step_id}] {status}: {goal}...")
        
        # Facts
        if 'facts' in state and state['facts']:
            self._write(f"║ facts: {len(state['facts'])}")
            for fact in state['facts'][:5]:
                self._write(f"║   - {fact[:70]}")
        
        # Artifacts
        if 'artifacts' in state and state['artifacts']:
            self._write(f"║ artifacts: {len(state['artifacts'])}")
        
        self._write("╚" + "═" * 78 + "╝")
        self._write("")
    
    def log_tool_call(self, tool_name: str, args: str, result_ok: bool, duration_ms: int):
        """Log a tool call."""
        status = "✅ SUCCESS" if result_ok else "❌ FAILED"
        self._write(f"🔧 Tool Call: {tool_name}({args[:50]}...) → {status} ({duration_ms}ms)")
    
    def log_tool_detail(self, tool_name: str, args: Dict, result: Any):
        """Log detailed tool call with full args and result."""
        self._write("\n" + "┌" + "─" * 78 + "┐")
        self._write(f"│ 🔧 TOOL: {tool_name}")
        self._write("├" + "─" * 78 + "┤")
        self._write("│ Arguments:")
        args_str = json.dumps(args, indent=2)
        for line in args_str.split('\n')[:20]:
            self._write(f"│   {line[:74]}")
        self._write("├" + "─" * 78 + "┤")
        self._write("│ Result:")
        
        if hasattr(result, 'ok'):
            self._write(f"│   Success: {result.ok}")
            if result.ok and hasattr(result, 'output'):
                output_str = str(result.output)[:500]
                for line in output_str.split('\n')[:10]:
                    self._write(f"│   {line[:74]}")
            elif hasattr(result, 'error'):
                self._write(f"│   Error: {result.error}")
        else:
            result_str = str(result)[:500]
            for line in result_str.split('\n')[:10]:
                self._write(f"│   {line[:74]}")
        
        self._write("└" + "─" * 78 + "┘")
        self._write("")
    
    def log_step_execution(self, step_id: str, step_goal: str, status: str):
        """Log step execution."""
        self._write(f"📋 Step {step_id}: {step_goal[:60]}... → {status}")
    
    def log_review_verdict(self, step_id: str, verdict: str, reason: str = ""):
        """Log a review verdict."""
        emoji = {"ok": "✅", "retry_step": "🔄", "replan": "🔀", "abort": "🛑"}.get(verdict, "❓")
        self._write(f"{emoji} Review: {step_id} → {verdict}")
        if reason:
            self._write(f"   Reason: {reason[:100]}")
    
    def log_iteration_increment(self, old_iter: int, new_iter: int, max_iter: int):
        """Log iteration counter increment."""
        self._write(f"🔢 Iteration: {old_iter} → {new_iter} (max: {max_iter})")
        if new_iter >= max_iter:
            self._write(f"⚠️  WARNING: Max iterations reached!")
    
    def log_limit_check(self, limit_name: str, current: int, max_value: int, exceeded: bool):
        """Log a limit check."""
        status = "❌ EXCEEDED" if exceeded else "✅ OK"
        self._write(f"📊 Limit Check: {limit_name}={current}/{max_value} → {status}")
    
    def log_error(self, error_msg: str):
        """Log an error."""
        self._write(f"❌ ERROR: {error_msg}")
    
    def log_custom(self, message: str):
        """Log a custom message."""
        self._write(f"💬 {message}")
    
    def finalize(self, status: str, total_tokens: int, duration_ms: int):
        """Write final summary."""
        self._write("\n" + "=" * 80)
        self._write(f"EXECUTION COMPLETE")
        self._write("=" * 80)
        self._write(f"Status: {status}")
        self._write(f"Total Duration: {duration_ms}ms ({duration_ms/1000:.2f}s)")
        self._write(f"Total Tokens: {total_tokens}")
        self._write(f"Trace File: {self.trace_file}")
        self._write("=" * 80)
        
        print(f"\n📝 Trace written to: {self.trace_file}")


# Global trace logger storage
_active_traces: Dict[str, TraceLogger] = {}


def get_trace_logger(run_id: str) -> TraceLogger:
    """Get or create trace logger for a run."""
    if run_id not in _active_traces:
        _active_traces[run_id] = TraceLogger(run_id)
    return _active_traces[run_id]


def remove_trace_logger(run_id: str):
    """Remove trace logger after run completes."""
    if run_id in _active_traces:
        del _active_traces[run_id]
