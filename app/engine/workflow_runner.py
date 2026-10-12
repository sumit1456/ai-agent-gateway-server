"""Bounded runtime for user-defined workflow graphs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage

from app.engine.prompts import get_prompt
from app.engine.workflow_templates import validate_definition


def _resolve_path(path: str, request: str, variables: dict, outputs: dict):
    if path == "$.request":
        return request
    if path == "$.variables":
        return variables
    if path == "$.steps":
        return outputs
    if not isinstance(path, str) or not path.startswith("$."):
        raise ValueError(f"Unsupported workflow input mapping: {path!r}")
    parts = path[2:].split(".")
    if len(parts) < 3 or parts[0] != "steps" or parts[2] not in {"output", "status"}:
        raise ValueError(f"Unsupported workflow input mapping: {path!r}")
    value = outputs.get(parts[1])
    if value is None:
        raise ValueError(f"Workflow input references unavailable step {parts[1]!r}")
    value = value if parts[2] == "output" else "succeeded"
    for key in parts[3:]:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"Workflow input path {path!r} was not present in prior output")
        value = value[key]
    return value


def _resolve_input(node: dict, request: str, variables: dict, outputs: dict) -> dict:
    return {
        key: _resolve_path(path, request, variables, outputs)
        for key, path in (node.get("input_mapping") or {"request": "$.request"}).items()
    }


def _parse_output(text: str, schema: dict) -> dict:
    """Normalize common model response shapes without rejecting useful JSON.

    output_schema is a prompt contract, not a reason to discard richer model output.
    Declared primitive types are coerced when safe; otherwise the original value is
    preserved so downstream steps can still inspect it.
    """
    text = text.strip()
    parsed = None
    try:
        start_candidates = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
        if start_candidates:
            start = min(start_candidates)
            parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        else:
            parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if parsed is None:
        if len(schema) == 1:
            return {next(iter(schema)): text}
        if not schema:
            return {"response": text}
        raise ValueError("Step returned text, but its output contract declares multiple fields")

    if isinstance(parsed, dict):
        output = parsed
        # A single declared output can contain a richer object than its starter
        # schema anticipated. Keep that object under the declared output key.
        if len(schema) == 1 and next(iter(schema)) not in output:
            output = {next(iter(schema)): output}
    elif len(schema) == 1:
        output = {next(iter(schema)): parsed}
    elif not schema and isinstance(parsed, dict):
        output = parsed
    else:
        output = {"response": parsed}

    for key, expected in schema.items():
        if key not in output:
            continue
        value = output[key]
        try:
            if expected == "boolean" and isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                output[key] = value.strip().lower() == "true"
            elif expected == "number" and isinstance(value, str):
                output[key] = float(value) if "." in value else int(value)
            elif expected == "array" and isinstance(value, str):
                decoded = json.loads(value)
                if isinstance(decoded, list):
                    output[key] = decoded
            elif expected == "object" and isinstance(value, str):
                decoded = json.loads(value)
                if isinstance(decoded, dict):
                    output[key] = decoded
        except (ValueError, TypeError, json.JSONDecodeError):
            # Preserve the model's original value; downstream mappings can still
            # inspect it and the run trace will show exactly what was returned.
            pass
    return output


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if pieces:
            return "\n".join(pieces)
    return json.dumps(content, ensure_ascii=False, default=str)


def _select_next(node_id: str, output: dict, edges: list[dict]) -> str | None:
    outgoing = [edge for edge in edges if edge.get("from") == node_id]
    if not outgoing:
        return None
    matched = []
    defaults = []
    for edge in outgoing:
        condition = edge.get("when")
        if not condition:
            defaults.append(edge)
            continue
        if not isinstance(condition, dict) or len(condition) != 1:
            raise ValueError(f"Invalid condition on edge from {node_id}")
        key, expected = next(iter(condition.items()))
        if output.get(key) == expected:
            matched.append(edge)
    if len(matched) == 1:
        return matched[0]["to"]
    if len(matched) > 1:
        raise ValueError(f"Multiple workflow branches matched after {node_id}")
    if len(defaults) == 1:
        return defaults[0]["to"]
    if not defaults:
        raise ValueError(f"No workflow branch matched after {node_id}")
    if len(defaults) > 1:
        raise ValueError(f"Workflow has ambiguous default branches after {node_id}")
    return None


async def run_workflow(ctx, run_id: str, definition: dict) -> dict:
    errors = validate_definition(definition)
    if errors:
        raise ValueError("Invalid workflow: " + "; ".join(errors))
    from app.db import SessionLocal
    from app.engine.runner import _publish_run_event
    from app.engine.trace_logger import get_trace_logger
    from app.models.tables import WorkflowRunStep
    from app.engine.tool_loop import run_tool_loop

    trace = get_trace_logger(run_id)
    nodes = {node["id"]: node for node in definition["nodes"]}
    outputs: dict[str, dict] = {}
    statuses: dict[str, str] = {}
    current = definition["start_node"]
    request = getattr(ctx, "input", "")
    variables = getattr(ctx, "variables", {}) or {}
    steps_taken = 0
    attempt_counts: dict[str, int] = {}
    transitions: list[dict] = []
    max_steps = min(int(definition.get("limits", {}).get("max_steps", 20)), ctx.config.limits.max_steps * 4)
    last_output = {}

    while current:
        if reason := ctx.check_limits():
            raise RuntimeError(f"Workflow stopped: {reason}")
        steps_taken += 1
        if steps_taken > max_steps:
            raise RuntimeError(f"Workflow exceeded its {max_steps}-step limit")
        node = nodes[current]
        attempt_counts[current] = attempt_counts.get(current, 0) + 1
        attempt = attempt_counts[current]
        if attempt > int(node.get("max_attempts", 1)):
            raise RuntimeError(f"Workflow step {current} exceeded its {node.get('max_attempts', 1)}-attempt limit")
        step_input = _resolve_input(node, request, variables, outputs)
        role = node["role"]
        if role in {"planner", "controller", "direct"}:
            # Planners and controllers need context to plan or route follow-up
            # turns; direct responders need it to answer conversationally.
            # Six messages gives at least three complete exchanges when present.
            recent = (getattr(ctx, "recent_messages", []) or [])[-6:]
            step_input["recent_history"] = [
                {"role": item.get("role"), "timestamp": item.get("timestamp"),
                 "content": item.get("content", "")}
                for item in recent
            ]
            # Show only tools that the workflow has explicitly assigned to at
            # least one step. Other steps still receive only their own tools.
            assigned_tool_names = list(dict.fromkeys(
                tool_name
                for workflow_node in definition["nodes"]
                for tool_name in (workflow_node.get("tools") or [])
            ))
            step_input["available_tools"] = [
                description for description in ctx.tools.describe()
                if description.split(":", 1)[0] in assigned_tool_names
            ]
        if role == "controller":
            step_input["allowed_next"] = [
                edge.get("when", {}).get("next")
                for edge in definition["edges"]
                if edge.get("from") == current and edge.get("when", {}).get("next")
            ]
        base = get_prompt(ctx.config, role, max_steps=ctx.config.limits.max_steps)
        system = f"{ctx.persona}\n\n{base}\n\nWORKFLOW STEP: {node.get('name', current)}\nThe workflow step instructions and output contract below take precedence over any conflicting role-template format.\n{node['instructions']}\nReturn a JSON object with these keys and value types: {json.dumps(node.get('output_schema') or {'response': 'string'})}."
        if role == "planner":
            system += (
                "\n\nUse the recent_history supplied in your input to resolve references to earlier turns. "
                "When the request needs older or date-filtered conversation details beyond that excerpt, "
                "make the plan explicitly assign get_conversation_history to an executor task, and name "
                "only tools listed in available_tools. Otherwise leave history retrieval out of the plan."
            )
        user = "WORKFLOW STEP INPUT:\n" + json.dumps(step_input, ensure_ascii=False, default=str)
        started = datetime.now(timezone.utc)
        row = WorkflowRunStep(run_id=UUID(run_id), node_id=current, attempt=attempt, status="running", input=step_input, started_at=started)
        async with SessionLocal() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            row_id = row.id
        trace.log_node_enter(current.upper(), {"node_id": current, "input_keys": list(step_input)})
        await _publish_run_event(run_id, "workflow_step_started", {"node_id": current, "attempt": attempt, "name": node.get("name", current), "role": role})
        try:
            available = set(ctx.tools.names())
            requested = node.get("tools") or []
            # Knowledge search is an optional workflow capability: presets may
            # include it, but steps should run normally when no KB is attached.
            if not ctx.kb_namespaces:
                requested = [name for name in requested if name != "semantic_search"]
            unknown = set(requested) - available
            if unknown:
                raise ValueError(f"Step {current} requests unavailable tools: {', '.join(sorted(unknown))}")
            tools = requested
            if tools:
                text, _ = await run_tool_loop(ctx, system, user, tools, role=role)
            else:
                response = await ctx.llm(role).ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
                ctx.usage.add_message(role, response)
                text = _content_to_text(response.content)
            output = _parse_output(text, node.get("output_schema") or {})
            finished = datetime.now(timezone.utc)
            async with SessionLocal() as db:
                stored = await db.get(WorkflowRunStep, row_id)
                stored.status = "succeeded"
                stored.output = output
                stored.finished_at = finished
                db.add(stored)
                await db.commit()
            outputs[current] = output
            statuses[current] = "succeeded"
            last_output = output
            trace.log_custom(f"Workflow step {current} completed")
            await _publish_run_event(run_id, "workflow_step_completed", {"node_id": current, "output": output})
        except Exception as exc:
            async with SessionLocal() as db:
                stored = await db.get(WorkflowRunStep, row_id)
                if stored:
                    stored.status = "failed"
                    stored.error = str(exc)[:2000]
                    stored.finished_at = datetime.now(timezone.utc)
                    db.add(stored)
                    await db.commit()
            await _publish_run_event(run_id, "workflow_step_failed", {"node_id": current, "error": str(exc)[:1000]})
            raise
        previous_node = current
        next_node = _select_next(current, last_output, definition["edges"])
        if next_node:
            edge = next(edge for edge in definition["edges"] if edge.get("from") == previous_node and edge.get("to") == next_node and (not edge.get("when") or all(last_output.get(key) == value for key, value in edge["when"].items())))
            transition = {"from": previous_node, "to": next_node, "condition": edge.get("when")}
            transitions.append(transition)
            await _publish_run_event(run_id, "workflow_transition", transition)
        current = next_node

    answer = last_output.get("response")
    if answer is None:
        answer = json.dumps(last_output, ensure_ascii=False, indent=2)
    elif not isinstance(answer, str):
        answer = json.dumps(answer, ensure_ascii=False, indent=2)
    state = {
        "workflow": {"schema_version": definition["schema_version"], "steps_taken": steps_taken, "statuses": statuses},
        "workflow_transitions": transitions,
        "workflow_outputs": outputs,
        "answer": str(answer), "stop_reason": "completed",
    }
    return state
