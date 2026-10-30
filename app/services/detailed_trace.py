"""Full-fidelity, per-run trace of the data passed through the agent pipeline."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.log_paths import get_log_dir
from app.config import settings


DETAILED_TRACE_LOGGING = settings.detailed_trace_logging


def _message_data(message: Any) -> dict[str, Any]:
    content = getattr(message, "content", str(message))
    if isinstance(content, list):
        content = [
            ({**part, "image_url": {"url": "[image omitted from trace]"}}
             if isinstance(part, dict) and part.get("type") == "image_url" else
             ({**part, "data": "[image omitted from trace]"}
              if isinstance(part, dict) and part.get("type") == "image" and "data" in part else part))
            for part in content
        ]
    data = {
        "type": type(message).__name__,
        "content": content,
    }
    for key in ("name", "tool_call_id", "tool_calls", "additional_kwargs", "response_metadata"):
        value = getattr(message, key, None)
        if value:
            data[key] = value
    return data


def _json_default(value: Any) -> str:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _tool_data(tool: Any) -> Any:
    if isinstance(tool, dict):
        return tool
    schema = getattr(tool, "args_schema", None)
    parameters = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
    return {
        "name": getattr(tool, "name", type(tool).__name__),
        "description": getattr(tool, "description", ""),
        "parameters": parameters,
    }


class DetailedTraceLogger:
    """Append complete inputs, model payloads, tool results, and outputs as JSONL."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.enabled = DETAILED_TRACE_LOGGING
        self._lock = threading.Lock()
        self.path: Path | None = None
        if self.enabled:
            log_dir = get_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            self.path = log_dir / f"detailed_trace_{run_id}.jsonl"
            self.record("trace_started", {"run_id": run_id})

    def record(self, event: str, data: Any) -> None:
        if not self.enabled or self.path is None:
            return
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": data,
        }
        try:
            line = json.dumps(entry, ensure_ascii=False, default=_json_default)
            with self._lock, self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()
        except (OSError, TypeError, ValueError):
            # Diagnostics must never break an agent run.
            return

    def llm_request(self, role: str, messages: list, tools: list | None = None, **metadata: Any) -> None:
        self.record("llm_request", {
            "role": role,
            "messages": [_message_data(message) for message in messages],
            "tools": [_tool_data(tool) for tool in (tools or [])],
            **metadata,
        })

    def llm_response(self, role: str, response: Any, **metadata: Any) -> None:
        self.record("llm_response", {
            "role": role,
            "response": _message_data(response) if hasattr(response, "content") else response,
            **metadata,
        })


_traces: dict[str, DetailedTraceLogger] = {}


def get_detailed_trace_logger(run_id: str) -> DetailedTraceLogger:
    trace = _traces.get(run_id)
    if trace is None:
        trace = DetailedTraceLogger(run_id)
        _traces[run_id] = trace
    return trace


def remove_detailed_trace_logger(run_id: str) -> None:
    _traces.pop(run_id, None)
