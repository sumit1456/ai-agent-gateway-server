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
        _node("plan", "Plan", "planner", "Return a complete, self-contained JSON plan in a steps array. Include concrete tasks, dependencies, required inputs, assigned tools, and success criteria. Executors will receive only this plan, so include the request details they need in the relevant task or input. Prior conversation messages are not automatically included; if needed, assign get_conversation_history to the executor task. Use semantic_search only when useful and available.", output_schema={"steps": "array"}),
        _node("execute", "Execute", "executor", "Complete the self-contained plan in the supplied steps. Call an assigned tool only when the plan requires it.", {"steps": "$.steps.plan.output.steps"}),
        {**_node("finalize", "Finalize", "finalizer", "Present the completed executor output clearly. Preserve useful details and do not add unsupported claims.", {"work": "$.steps.execute.output"}), "return_input": "work"},
    ],
    "edges": [{"from": "plan", "to": "execute"}, {"from": "execute", "to": "finalize"}],
    "limits": {"max_steps": 12, "max_retries_per_step": 1},
}
SIMPLE["nodes"][1]["tools"] = ["get_conversation_history", "semantic_search"]

REVIEWED = {
    "schema_version": 1, "start_node": "plan",
    "nodes": [
        _node("plan", "Plan", "planner", "Return a complete, self-contained JSON plan in a steps array. Include concrete tasks, dependencies, required inputs, assigned tools, and success criteria. Executors will receive only this plan, so include the request details they need in the relevant task or input. Prior conversation messages are not automatically included; if needed, assign get_conversation_history to the executor task. Use semantic_search only when useful and available.", output_schema={"steps": "array"}),
        _node("execute", "Execute", "executor", "Complete the self-contained plan in the supplied steps. Call an assigned tool only when the plan requires it.", {"steps": "$.steps.plan.output.steps"}),
        _node("review", "Review", "reviewer", "Review the plan and execution output against the stated goals and criteria. Return JSON with passed (boolean) and feedback (string).", {"plan": "$.steps.plan.output.steps", "work": "$.steps.execute.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Finalize", "finalizer", "Present the reviewed executor output clearly, applying review feedback where appropriate.", {"work": "$.steps.execute.output", "review": "$.steps.review.output"}),
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

WORKFLOW_DESIGNER = {
    "schema_version": 1, "start_node": "understand_brief",
    "nodes": [
        _node(
            "understand_brief", "Understand the brief", "planner",
            "Translate the user's overview into a workflow specification. Identify the workflow's purpose, trigger/request, required inputs, expected result, constraints, and any tools explicitly named by the user. Do not invent registered tool names. Record assumptions and unresolved questions instead of silently guessing. Return JSON with specification (object), assumptions (array), and questions (array).",
            {"brief": "$.request"}, {"specification": "object", "assumptions": "array", "questions": "array"},
        ),
        _node(
            "draft_workflow", "Draft the workflow", "executor",
            "Create an editable workflow definition from the specification. Use only supported node roles: planner, executor, reviewer, direct, controller, router, and finalizer. Give every node a unique stable id, useful name, clear instructions, JSON input_mapping, JSON output_schema, explicit tools list, and max_attempts from 1 to 3. Map later-node inputs only to the original request, variables, or outputs of earlier nodes. Add valid edges and make every node reachable from start_node. Use no tools unless the user explicitly named a tool. Return the complete definition under workflow (object), plus rationale (string).",
            {"brief": "$.request", "specification": "$.steps.understand_brief.output.specification", "assumptions": "$.steps.understand_brief.output.assumptions", "questions": "$.steps.understand_brief.output.questions"},
            {"workflow": "object", "rationale": "string"},
        ),
        _node(
            "review_workflow", "Check the workflow", "reviewer",
            "Check the draft against the specification and supported workflow format. Verify start_node, unique node ids, supported roles, non-empty instructions, valid input references, valid edges, and reachability. Check that output_schema values are types rather than source paths, and that tools were named by the user. Fix issues directly in the workflow definition. Return workflow (object), valid (boolean), and notes (array).",
            {"specification": "$.steps.understand_brief.output.specification", "draft": "$.steps.draft_workflow.output.workflow", "rationale": "$.steps.draft_workflow.output.rationale"},
            {"workflow": "object", "valid": "boolean", "notes": "array"},
        ),
        _node(
            "present_draft", "Present the editable draft", "finalizer",
            "Present the reviewed workflow definition as JSON, followed by a concise explanation of its steps, assumptions, unresolved questions, and any tools that must be registered or assigned. This workflow only drafts a configuration; do not claim it has been saved or deployed. Preserve the workflow JSON exactly so the user can copy and edit it.",
            {"workflow": "$.steps.review_workflow.output.workflow", "valid": "$.steps.review_workflow.output.valid", "notes": "$.steps.review_workflow.output.notes", "assumptions": "$.steps.understand_brief.output.assumptions", "questions": "$.steps.understand_brief.output.questions"},
            {"workflow": "object", "explanation": "string"},
        ),
    ],
    "edges": [
        {"from": "understand_brief", "to": "draft_workflow"},
        {"from": "draft_workflow", "to": "review_workflow"},
        {"from": "review_workflow", "to": "present_draft"},
    ],
    "limits": {"max_steps": 4, "max_retries_per_step": 1},
}

AGENT_DESIGNER = {
    "schema_version": 1, "start_node": "understand_agent_brief",
    "nodes": [
        _node(
            "understand_agent_brief", "Understand the agent brief", "planner",
            "Translate the user's overview into an agent specification. Identify agent name and purpose, users, example requests, inputs, expected responses, constraints, and tools or knowledge needs explicitly mentioned. Do not invent registered tools or claim credentials are configured. Record assumptions and unresolved questions. Return JSON with specification (object), assumptions (array), and questions (array).",
            {"brief": "$.request"}, {"specification": "object", "assumptions": "array", "questions": "array"},
        ),
        _node(
            "design_agent", "Design the agent and workflow", "executor",
            "Create a draft agent identity and a complete editable workflow from the specification. Return agent (object) with name, purpose, and a concise description, and workflow (object) using schema_version 1. The workflow must use supported roles, unique ids, non-empty semantic instructions, JSON input_mapping and output_schema, explicit tools arrays, reachable nodes, valid edges, and max_attempts from 1 to 3. Use only tools explicitly named in the brief; otherwise use empty tool lists and identify the tool or knowledge-base requirements as setup notes. Do not invent provider credentials, model IDs, or claim anything has been saved. Include rationale (string) and setup_notes (array).",
            {"brief": "$.request", "specification": "$.steps.understand_agent_brief.output.specification", "assumptions": "$.steps.understand_agent_brief.output.assumptions", "questions": "$.steps.understand_agent_brief.output.questions"},
            {"agent": "object", "workflow": "object", "rationale": "string", "setup_notes": "array"},
        ),
        _node(
            "review_agent", "Review the agent draft", "reviewer",
            "Review the agent identity and workflow for consistency with the specification. Validate workflow structure, node ids, supported roles, input references, output type declarations, edges, reachability, tool assignments, and unsupported assumptions. Correct the workflow directly. Do not create model IDs or claim the result is persisted. Return agent (object), workflow (object), valid (boolean), and notes (array).",
            {"specification": "$.steps.understand_agent_brief.output.specification", "agent": "$.steps.design_agent.output.agent", "workflow": "$.steps.design_agent.output.workflow", "setup_notes": "$.steps.design_agent.output.setup_notes"},
            {"agent": "object", "workflow": "object", "valid": "boolean", "notes": "array"},
        ),
        _node(
            "present_agent_draft", "Present the editable agent draft", "finalizer",
            "Present the reviewed agent identity and workflow JSON, then list setup notes, assumptions, unresolved questions, and the workflow roles for which the user must choose configured models. Explain that the user must select their configured provider/models and save this workflow and agent in the UI. Do not claim that the agent has been created, saved, or deployed. Preserve the JSON so it can be copied and edited.",
            {"agent": "$.steps.review_agent.output.agent", "workflow": "$.steps.review_agent.output.workflow", "valid": "$.steps.review_agent.output.valid", "notes": "$.steps.review_agent.output.notes", "assumptions": "$.steps.understand_agent_brief.output.assumptions", "questions": "$.steps.understand_agent_brief.output.questions"},
            {"agent": "object", "workflow": "object", "explanation": "string"},
        ),
    ],
    "edges": [
        {"from": "understand_agent_brief", "to": "design_agent"},
        {"from": "design_agent", "to": "review_agent"},
        {"from": "review_agent", "to": "present_agent_draft"},
    ],
    "limits": {"max_steps": 4, "max_retries_per_step": 1},
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
        _node("plan", "Plan the work", "planner", "Create a concise actionable plan. Prior conversation messages are not automatically included; if the request depends on earlier turns, explicitly assign get_conversation_history to the executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless needed."),
        _node("execute", "Execute the plan", "executor", "Complete the planned task, calling only tools assigned to this step when the plan requires them.", {"request": "$.request", "plan": "$.steps.plan.output"}),
        _node("review_plan", "Plan work for review", "planner", "Create a concise actionable plan for a task that benefits from verification. Prior conversation messages are not automatically included; if the request depends on earlier turns, explicitly assign get_conversation_history to the executor task. If an attached knowledge base is relevant, explicitly assign semantic_search. Do not retrieve either source unless needed."),
        _node("review_execute", "Execute reviewed work", "executor", "Complete the planned task, calling only tools assigned to this step when the plan requires them.", {"request": "$.request", "plan": "$.steps.review_plan.output"}),
        _node("review", "Review the result", "reviewer", "Check the completed work against the request. Return JSON with passed (boolean) and feedback (string).", {"request": "$.request", "work": "$.steps.review_execute.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Present the result", "finalizer", "Present the result from the selected path clearly.", {"request": "$.request", "completed_steps": "$.steps"}),
    ],
    "edges": [
        {"from": "choose_path", "to": "direct", "when": {"next": "direct"}},
        {"from": "choose_path", "to": "plan", "when": {"next": "plan"}},
        {"from": "choose_path", "to": "review_plan", "when": {"next": "reviewed_plan"}},
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
    {"id": "workflow-designer", "name": "AI Workflow Designer", "description": "Turn a plain-language brief into a reviewed, editable workflow JSON draft.", "definition": WORKFLOW_DESIGNER},
    {"id": "agent-designer", "name": "AI Agent Designer", "description": "Turn a plain-language brief into an agent profile and a reviewed, editable workflow JSON draft.", "definition": AGENT_DESIGNER},
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
        return_input = node.get("return_input")
        if return_input is not None and (
            node.get("role") != "finalizer"
            or not isinstance(return_input, str)
            or not return_input.strip()
            or return_input not in (node.get("input_mapping") or {})
        ):
            errors.append(f"Node {node.get('id')} return_input must name a mapped finalizer input")
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
