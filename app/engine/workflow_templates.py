"""Built-in workflow starters and definition validation."""
from __future__ import annotations

import copy


def _node(node_id: str, name: str, role: str, instructions: str, input_mapping=None, output_schema=None):
    return {
        "id": node_id, "type": "agent", "name": name, "role": role,
        "instructions": instructions,
        "input_mapping": input_mapping or {"request": "$.request"},
        "output_schema": output_schema or {"response": "string"},
        "tools": [], "max_attempts": 1,
    }


SIMPLE = {
    "schema_version": 1, "start_node": "plan",
    "nodes": [
        _node("plan", "Plan", "planner", "Break the request into a concise, actionable plan. Use recent conversation context when relevant. If older session history is needed, explicitly assign get_conversation_history to the executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless the request needs it."),
        _node("execute", "Execute", "executor", "Complete the task using the request and plan. Call an assigned tool only when the plan requires it.", {"request": "$.request", "plan": "$.steps.plan.output"}),
        _node("finalize", "Finalize", "finalizer", "Present the completed result clearly.", {"request": "$.request", "work": "$.steps.execute.output"}),
    ],
    "edges": [{"from": "plan", "to": "execute"}, {"from": "execute", "to": "finalize"}],
    "limits": {"max_steps": 12, "max_retries_per_step": 1},
}
SIMPLE["nodes"][1]["tools"] = ["get_conversation_history", "semantic_search"]

REVIEWED = {
    "schema_version": 1, "start_node": "plan",
    "nodes": [
        _node("plan", "Plan", "planner", "Break the request into a concise, actionable plan. Use recent conversation context when relevant. If older session history is needed, explicitly assign get_conversation_history to the executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless the request needs it."),
        _node("execute", "Execute", "executor", "Complete the task using the request and plan. Call an assigned tool only when the plan requires it.", {"request": "$.request", "plan": "$.steps.plan.output"}),
        _node("review", "Review", "reviewer", "Review the work. Return a JSON object with passed (boolean) and feedback (string).", {"request": "$.request", "work": "$.steps.execute.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Finalize", "finalizer", "Present the reviewed result clearly.", {"request": "$.request", "work": "$.steps.execute.output", "review": "$.steps.review.output"}),
    ],
    "edges": [{"from": "plan", "to": "execute"}, {"from": "execute", "to": "review"}, {"from": "review", "to": "finalize"}],
    "limits": {"max_steps": 16, "max_retries_per_step": 1},
}
REVIEWED["nodes"][1]["tools"] = ["get_conversation_history", "semantic_search"]

VIDEO_BLOG = {
    "schema_version": 1, "start_node": "transcript",
    "nodes": [
        _node("transcript", "Prepare transcript", "executor", "Use an enabled transcript retrieval tool if one is configured for this step. Otherwise, use transcript text included in the request or variables and explain if only a video URL was supplied without a retrieval tool. Clean and organize the transcript.", {"request": "$.request", "variables": "$.variables"}, {"transcript": "string"}),
        _node("outline", "Plan the blog post", "planner", "Based on the transcript, propose a target audience, several title options, a meta description, and a detailed outline.", {"transcript": "$.steps.transcript.output.transcript"}, {"titles": "array", "description": "string", "outline": "string"}),
        _node("draft", "Write the post", "executor", "Write a polished blog post based on the transcript and approved content plan. Do not add unsupported claims.", {"transcript": "$.steps.transcript.output.transcript", "content_plan": "$.steps.outline.output"}, {"title": "string", "description": "string", "post": "string"}),
        _node("review", "Review the post", "reviewer", "Check that the post is clear, useful, well structured, and faithful to the transcript. Return JSON with passed (boolean) and feedback (string).", {"transcript": "$.steps.transcript.output.transcript", "draft": "$.steps.draft.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Prepare final post", "finalizer", "Return the final blog post with title and description, applying reviewer feedback where appropriate.", {"draft": "$.steps.draft.output", "review": "$.steps.review.output"}, {"title": "string", "description": "string", "post": "string"}),
    ],
    "edges": [{"from": "transcript", "to": "outline"}, {"from": "outline", "to": "draft"}, {"from": "draft", "to": "review"}, {"from": "review", "to": "finalize"}],
    "limits": {"max_steps": 20, "max_retries_per_step": 1},
}

# The adaptive option makes the controller an explicit workflow node. It can only
# choose one of the transitions defined below.
ADAPTIVE = {
    "schema_version": 1, "start_node": "controller",
    "nodes": [
        _node("controller", "Choose the next role", "controller", "You control this workflow. Choose exactly one value from the allowed_next list in your input. On the first call choose direct for simple requests or plan for multi-step work. After a plan choose execute. After execution choose review when verification is useful, otherwise finalize. After review choose finalize, or execute to revise. Return JSON with the key next.", {"request": "$.request", "completed_steps": "$.steps"}, {"next": "string"}),
        _node("direct", "Answer directly", "direct", "Answer the request directly.", {"request": "$.request"}),
        _node("plan", "Plan", "planner", "Create an actionable plan using recent conversation context. If older session history is needed, explicitly assign get_conversation_history to an executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless the request needs it.", {"request": "$.request", "completed_steps": "$.steps"}),
        _node("execute", "Execute", "executor", "Carry out the plan, incorporating any review feedback. Call a tool only when the plan requires it and the tool is assigned to this step.", {"request": "$.request", "completed_steps": "$.steps"}),
        _node("review", "Review", "reviewer", "Check the latest work against the request. Return JSON with passed (boolean) and feedback (string).", {"request": "$.request", "completed_steps": "$.steps"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Finalize", "finalizer", "Present the final result using the completed work and review findings.", {"request": "$.request", "completed_steps": "$.steps"}),
    ],
    "edges": [
        {"from": "controller", "to": "direct", "when": {"next": "direct"}},
        {"from": "controller", "to": "plan", "when": {"next": "plan"}},
        {"from": "controller", "to": "execute", "when": {"next": "execute"}},
        {"from": "controller", "to": "review", "when": {"next": "review"}},
        {"from": "controller", "to": "finalize", "when": {"next": "finalize"}},
        {"from": "direct", "to": "finalize"},
        {"from": "plan", "to": "execute"},
        {"from": "execute", "to": "controller"},
        {"from": "review", "to": "controller"},
    ],
    "limits": {"max_steps": 16, "max_retries_per_step": 1},
}
ADAPTIVE["nodes"][2]["tools"] = ["get_conversation_history", "semantic_search"]
ADAPTIVE["nodes"][1]["tools"] = ["get_conversation_history", "semantic_search"]
ADAPTIVE["nodes"][0]["max_attempts"] = 6
ADAPTIVE["nodes"][2]["max_attempts"] = 3
ADAPTIVE["nodes"][3]["max_attempts"] = 4
ADAPTIVE["nodes"][4]["max_attempts"] = 3

# Dynamic starts with one routing decision, then follows the selected path to
# completion. Each route is explicit and editable in the workflow builder.
DYNAMIC = {
    "schema_version": 1, "start_node": "choose_path",
    "nodes": [
        _node(
            "choose_path", "Choose a path", "controller",
            "Choose exactly one next value from the allowed_next list. Use direct for a simple request; use plan for work that benefits from a plan; use reviewed_plan when the task is complex, factual, or benefits from a quality check. The workflow will continue along the selected path to completion. Return JSON with key next.",
            {"request": "$.request"}, {"next": "string"},
        ),
        _node("direct", "Answer directly", "direct", "Answer the request directly and use only tools assigned to this step."),
        _node("plan", "Plan the work", "planner", "Create a concise actionable plan. Use recent conversation context when relevant. If older session history is needed, explicitly assign get_conversation_history to the executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless the request needs it."),
        _node("execute", "Execute the plan", "executor", "Complete the planned task, calling only tools assigned to this step when the plan requires them.", {"request": "$.request", "plan": "$.steps.plan.output"}),
        _node("review_plan", "Plan work for review", "planner", "Create a concise actionable plan for a task that benefits from verification. Use recent conversation context when relevant. If older session history is needed, explicitly assign get_conversation_history to the executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless the request needs it."),
        _node("review_execute", "Execute reviewed work", "executor", "Complete the planned task, calling only tools assigned to this step when the plan requires them.", {"request": "$.request", "plan": "$.steps.review_plan.output"}),
        _node("review", "Review the result", "reviewer", "Check the completed work against the request. Return JSON with passed (boolean) and feedback (string).", {"request": "$.request", "work": "$.steps.review_execute.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Present the result", "finalizer", "Present the result from the selected path clearly.", {"request": "$.request", "completed_steps": "$.steps"}),
    ],
    "edges": [
        {"from": "choose_path", "to": "direct", "when": {"next": "direct"}},
        {"from": "choose_path", "to": "plan", "when": {"next": "plan"}},
        {"from": "choose_path", "to": "review_plan", "when": {"next": "reviewed_plan"}},
        {"from": "direct", "to": "finalize"},
        {"from": "plan", "to": "execute"},
        {"from": "execute", "to": "finalize"},
        {"from": "review_plan", "to": "review_execute"},
        {"from": "review_execute", "to": "review"},
        {"from": "review", "to": "finalize"},
    ],
    "limits": {"max_steps": 12, "max_retries_per_step": 1},
}
DYNAMIC["nodes"][1]["tools"] = ["get_conversation_history", "semantic_search"]
DYNAMIC["nodes"][3]["tools"] = ["get_conversation_history", "semantic_search"]
DYNAMIC["nodes"][5]["tools"] = ["get_conversation_history", "semantic_search"]

PRESETS = [
    {"id": "simple", "name": "Plan → Execute → Finalize", "description": "A straightforward workflow for general multi-step tasks.", "definition": SIMPLE},
    {"id": "reviewed", "name": "Plan → Execute → Review → Finalize", "description": "Adds a separate quality check before the result is presented.", "definition": REVIEWED},
    {"id": "adaptive", "name": "Adaptive direct or planned workflow", "description": "Starts with a routing decision to select a direct answer or planned work.", "definition": ADAPTIVE},
    {"id": "dynamic", "name": "Dynamic workflow", "description": "A routing agent chooses a direct, planned, or reviewed path, then the workflow follows that path to completion. Use this for general-purpose requests.", "definition": DYNAMIC},
    {"id": "video-blog", "name": "Video transcript → Blog post", "description": "Prepare a transcript, plan a blog post, draft it, review it, and format the final post.", "definition": VIDEO_BLOG},
]


def get_preset(preset_id: str) -> dict | None:
    item = next((p for p in PRESETS if p["id"] == preset_id), None)
    return copy.deepcopy(item) if item else None


def validate_definition(definition: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(definition, dict) or definition.get("schema_version") != 1:
        return ["schema_version must be 1"]
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not nodes:
        return ["nodes must be a non-empty list"]
    if not isinstance(edges, list):
        return ["edges must be a list"]
    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(nodes) or any(not isinstance(i, str) or not i for i in ids):
        errors.append("Every node must have a non-empty string id")
    if len(set(ids)) != len(ids):
        errors.append("Node ids must be unique")
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    if definition.get("start_node") not in by_id:
        errors.append("start_node must reference a node")
    for node in nodes:
        if node.get("type") != "agent":
            errors.append(f"Node {node.get('id')} has unsupported type; only agent nodes are currently supported")
        if node.get("role") not in {"router", "planner", "executor", "reviewer", "direct", "finalizer", "controller"}:
            errors.append(f"Node {node.get('id')} has an unsupported role")
        if not isinstance(node.get("instructions"), str) or not node["instructions"].strip():
            errors.append(f"Node {node.get('id')} needs instructions")
        if not isinstance(node.get("input_mapping", {}), dict):
            errors.append(f"Node {node.get('id')} input_mapping must be an object")
        if not isinstance(node.get("output_schema", {}), dict):
            errors.append(f"Node {node.get('id')} output_schema must be an object")
        if not isinstance(node.get("tools", []), list) or any(not isinstance(tool, str) for tool in node.get("tools", [])):
            errors.append(f"Node {node.get('id')} tools must be a list of names")
        if not isinstance(node.get("max_attempts", 1), int) or not 1 <= node.get("max_attempts", 1) <= 3:
            errors.append(f"Node {node.get('id')} max_attempts must be between 1 and 3")
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") not in by_id or edge.get("to") not in by_id:
            errors.append("Every edge must reference existing from/to nodes")
    if not errors:
        reachable = {definition["start_node"]}
        while True:
            more = {e["to"] for e in edges if e["from"] in reachable}
            updated = reachable | more
            if updated == reachable:
                break
            reachable = updated
        if reachable != set(by_id):
            errors.append("Every node must be reachable from start_node")
        if not any(not any(e["from"] == node_id for e in edges) for node_id in by_id):
            errors.append("Workflow needs at least one terminal node")
    limits = definition.get("limits", {})
    try:
        max_steps = int(limits.get("max_steps", 20)) if isinstance(limits, dict) else 0
    except (TypeError, ValueError):
        max_steps = 0
    if not 1 <= max_steps <= 100:
        errors.append("limits.max_steps must be between 1 and 100")
    return errors
