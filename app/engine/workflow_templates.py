"""Built-in workflow starters and definition validation."""
from __future__ import annotations

import copy


_ROLE_TOOL_POLICIES = {
    "controller": (
        "Tool policy: do not call tools. For a simple request, return the final answer in response with next set to null. "
        "Otherwise choose exactly one transition from the allowed_next values in the input."
    ),
    "planner": (
        "Tool policy: the available_tools input is the authoritative catalog for this workflow. "
        "Assign only exact names from that list, only when a step needs them, and leave the tools list empty otherwise. "
        "Never invent tool names or mistake output fields such as response for tools."
    ),
    "executor": (
        "Tool policy: use only tools assigned to this step and advertised in the current request, with exact names. "
        "Never invent tool names or mistake output fields such as response for tools. "
        "If no assigned tool applies, proceed without a tool when possible or report the missing capability."
    ),
    "direct": (
        "Tool policy: use only tools assigned to this step and advertised in the current request, with exact names. "
        "Never invent tool names or mistake output fields such as response for tools. "
        "If no assigned tool is relevant, answer without a tool."
    ),
    "reviewer": "Tool policy: do not call tools; review only the evidence included in your input.",
    "finalizer": "Tool policy: do not call tools; use only the supplied request, results, and evidence.",
}


def _node(node_id: str, name: str, role: str, instructions: str, input_mapping=None, output_schema=None):
    return {
        "id": node_id, "type": "agent", "name": name, "role": role,
        "instructions": f"{instructions.rstrip()}\n\n{_ROLE_TOOL_POLICIES.get(role, '')}".strip(),
        "input_mapping": input_mapping or {"request": "$.request"},
        "output_schema": output_schema or {"response": "string"},
        "tools": [], "max_attempts": 1,
    }


SIMPLE = {
    "schema_version": 1, "start_node": "plan",
    "nodes": [
        _node("plan", "Plan", "planner", "Return a complete, self-contained JSON plan in a steps array. Include concrete tasks, dependencies, required inputs, assigned tools, and success criteria. Executors will receive only this plan, so include the request details they need in the relevant task or input. Prior conversation messages are not automatically included; assign get_conversation_history only if it appears in available_tools and the request needs earlier context. Use semantic_search only if it appears in available_tools and knowledge retrieval is useful.", output_schema={"steps": "array"}),
        _node("execute", "Execute", "executor", "Complete the self-contained plan in the supplied steps. Call an assigned tool only when the plan requires it.", {"steps": "$.steps.plan.output.steps"}),
        {**_node("finalize", "Finalize", "finalizer", "Present the completed executor output clearly. Preserve useful details and do not add unsupported claims.", {"work": "$.steps.execute.output"}), "return_input": "work"},
    ],
    "edges": [{"from": "plan", "to": "execute"}, {"from": "execute", "to": "finalize"}],
    "limits": {"max_steps": 12, "max_retries_per_step": 1},
}
REVIEWED = {
    "schema_version": 1, "start_node": "plan",
    "nodes": [
        _node("plan", "Plan", "planner", "Return a complete, self-contained JSON plan in a steps array. Include concrete tasks, dependencies, required inputs, assigned tools, and success criteria. Executors will receive only this plan, so include the request details they need in the relevant task or input. Prior conversation messages are not automatically included; assign get_conversation_history only if it appears in available_tools and the request needs earlier context. Use semantic_search only if it appears in available_tools and knowledge retrieval is useful.", output_schema={"steps": "array"}),
        _node("execute", "Execute", "executor", "Complete the self-contained plan in the supplied steps. Call an assigned tool only when the plan requires it.", {"steps": "$.steps.plan.output.steps"}),
        _node("review", "Review", "reviewer", "Review the plan and execution output against the stated goals and criteria. Return JSON with passed (boolean) and feedback (string).", {"plan": "$.steps.plan.output.steps", "work": "$.steps.execute.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Finalize", "finalizer", "Present the reviewed executor output clearly, applying review feedback where appropriate.", {"work": "$.steps.execute.output", "review": "$.steps.review.output"}),
    ],
    "edges": [{"from": "plan", "to": "execute"}, {"from": "execute", "to": "review"}, {"from": "review", "to": "finalize"}],
    "limits": {"max_steps": 16, "max_retries_per_step": 1},
}
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
            "Create an editable schema_version 1 workflow definition. Every node must have a unique id, type agent, name, supported role, non-empty instructions, input_mapping object, output_schema object, tools array, and max_attempts from 1 to 3. Supported roles are planner, executor, reviewer, direct, controller, router, and finalizer. Treat input_mapping keys as the exact JSON fields a node receives and values as source paths. Valid sources are $.request (user request string), $.variables (agent variables object), $.steps, $.steps.<earlier_node_id>.output, a nested key under an earlier node's output, or $.steps.<earlier_node_id>.status. The start node can map only $.request and/or $.variables. Later nodes receive only mapped fields; never assume prior outputs are passed automatically. Treat every output_schema key as required. Return exactly one JSON object with all those keys and values of the declared types (string, number, integer, boolean, object, array); output_schema values are types, never paths. Do not return prose or Markdown outside that object. Use empty tools arrays unless the user explicitly names an available tool; never invent tool names. Follow the working Simple preset pattern: planner output maps into executor input using $.steps.<plan_id>.output.<field>, and the finalizer maps the executor's complete output. Follow the Reviewed preset pattern by mapping plan and work into a reviewer that returns passed:boolean and feedback:string. For branching, a controller receives allowed_next and can either answer a simple request in the same call with next:null and response:string, or route with next set to an allowed value and response:null; declare next and response as nullable string fields. Do not create a separate direct node solely to answer simple requests. Other conditions must match declared output keys and types. Make each transition unambiguous and provide at most one default edge. When a brief requests N retries, implement exactly N bounded rework/re-review opportunities after the first review and add a terminal route after the final retry; do not rely only on max_steps or max_attempts to end a loop. Include schema_version, start_node, nodes, edges, and limits with max_steps from 1 to 100. All nodes must be reachable and edges must reference existing ids. Also create test_run_request as an API run body with a realistic non-empty input string and variables object, tailored to the first node's input_mapping. Return the complete definition under workflow (object), test_run_request (object), plus rationale (string).",
            {"brief": "$.request", "specification": "$.steps.understand_brief.output.specification", "assumptions": "$.steps.understand_brief.output.assumptions", "questions": "$.steps.understand_brief.output.questions"},
            {"workflow": "object", "test_run_request": "object", "rationale": "string"},
        ),
        _node(
            "review_workflow", "Check the workflow", "reviewer",
            "Check the workflow and sample test request against the specification and actual runtime. Verify schema_version 1, start_node, unique node ids, type agent, supported roles, non-empty instructions, and every node input_mapping: keys are the node's actual input field names; each value is a supported path that exists earlier in that workflow; and every field used by its instructions is mapped. Verify every output_schema key is required by the node prompt, uses one supported type, and is returned by downstream references under the correct key. Check test_run_request.input is a non-empty string and variables is an object that can supply the first node's mapped $.request and/or $.variables; the start node cannot depend on prior steps. Also verify tools arrays, max_attempts 1-3, max_steps 1-100, valid edge endpoints, reachability, and a terminal node. Verify tools were named by the user and do not invent them. For controller nodes, require next:string and matching when.next edges; for other conditional edges, require conditions to match declared outputs and be unambiguous. Use the Simple, Reviewed, and Dynamic presets as working patterns for sequential, reviewed, and branched flows. Fix workflow and test_run_request directly. Return workflow (object), test_run_request (object), valid (boolean), and notes (array).",
            {"specification": "$.steps.understand_brief.output.specification", "draft": "$.steps.draft_workflow.output.workflow", "test_run_request": "$.steps.draft_workflow.output.test_run_request", "rationale": "$.steps.draft_workflow.output.rationale"},
            {"workflow": "object", "test_run_request": "object", "valid": "boolean", "notes": "array"},
        ),
        _node(
            "present_draft", "Present the editable draft", "finalizer",
            "Present the reviewed workflow definition and test_run_request as separate JSON objects, followed by a concise explanation of the steps, assumptions, unresolved questions, and required tool setup. The test_run_request must be a usable POST /v1/agents/{agent_id}/run request body with input and variables. This workflow only drafts a configuration; do not claim it has been saved or deployed. Preserve both JSON objects exactly so the user can copy and use them.",
            {"workflow": "$.steps.review_workflow.output.workflow", "test_run_request": "$.steps.review_workflow.output.test_run_request", "valid": "$.steps.review_workflow.output.valid", "notes": "$.steps.review_workflow.output.notes", "assumptions": "$.steps.understand_brief.output.assumptions", "questions": "$.steps.understand_brief.output.questions"},
            {"workflow": "object", "test_run_request": "object", "explanation": "string"},
        ),
    ],
    "edges": [
        {"from": "understand_brief", "to": "draft_workflow"},
        {"from": "draft_workflow", "to": "review_workflow"},
        {"from": "review_workflow", "to": "present_draft"},
    ],
    "limits": {"max_steps": 4, "max_retries_per_step": 1},
}

# The adaptive option makes the controller an explicit workflow node. It can only
# choose one of the transitions defined below.
ADAPTIVE = {
    "schema_version": 1, "start_node": "controller",
    "nodes": [
        _node("controller", "Choose the next role", "controller", "You control this workflow. On the first call, answer simple requests directly in the response field. For multi-step work choose plan. After a plan choose execute. After execution choose review when verification is useful, otherwise finalize. After review choose finalize, or execute to revise. When answering directly set next to null; when routing set response to null and choose exactly one allowed_next value.", {"request": "$.request", "completed_steps": "$.steps"}, {"next": ["string", "null"], "response": ["string", "null"]}),
        _node("plan", "Plan", "planner", "Create an actionable plan using the supplied recent conversation context. If older session history is needed, assign get_conversation_history only if it appears in available_tools. If an attached knowledge base is relevant, assign semantic_search only if it appears in available_tools. Otherwise note that the workflow has no tool assigned for that capability.", {"request": "$.request", "completed_steps": "$.steps"}),
        _node("execute", "Execute", "executor", "Carry out the plan, incorporating any review feedback. Call a tool only when the plan requires it and the tool is assigned to this step.", {"request": "$.request", "completed_steps": "$.steps"}),
        _node("review", "Review", "reviewer", "Check the latest work against the request. Return JSON with passed (boolean) and feedback (string).", {"request": "$.request", "completed_steps": "$.steps"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Finalize", "finalizer", "Present the final result using the completed work and review findings.", {"request": "$.request", "completed_steps": "$.steps"}),
    ],
    "edges": [
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
ADAPTIVE["nodes"][0]["max_attempts"] = 3
for _node_id in ("controller", "plan", "execute", "review"):
    next(node for node in ADAPTIVE["nodes"] if node["id"] == _node_id)["max_attempts"] = 3

# Dynamic starts with one routing decision, then follows the selected path to
# completion. Each route is explicit and editable in the workflow builder.
DYNAMIC = {
    "schema_version": 1, "start_node": "choose_path",
    "nodes": [
        _node(
            "choose_path", "Choose a path", "controller",
            "For a simple request, answer it directly in response and set next to null. For work that benefits from a plan, choose plan; for complex, factual, or verification-sensitive work choose reviewed_plan. When choosing a route, set response to null and choose exactly one allowed_next value. The workflow will continue along the selected path to completion.",
            {"request": "$.request"}, {"next": ["string", "null"], "response": ["string", "null"]},
        ),
        _node("plan", "Plan the work", "planner", "Create a concise actionable plan. Use only exact names in available_tools. Assign get_conversation_history only if it appears in available_tools and earlier context is needed; assign semantic_search only if it appears in available_tools and knowledge retrieval is useful. If a required capability is not listed, note that instead of inventing a tool."),
        _node("execute", "Execute the plan", "executor", "Complete the planned task, calling only tools assigned to this step when the plan requires them.", {"request": "$.request", "plan": "$.steps.plan.output"}),
        _node("review_plan", "Plan work for review", "planner", "Create a concise actionable plan for a task that benefits from verification. Use only exact names in available_tools. Assign get_conversation_history only if it appears in available_tools and earlier context is needed; assign semantic_search only if it appears in available_tools and knowledge retrieval is useful. If a required capability is not listed, note that instead of inventing a tool."),
        _node("review_execute", "Execute reviewed work", "executor", "Complete the planned task, calling only tools assigned to this step when the plan requires them.", {"request": "$.request", "plan": "$.steps.review_plan.output"}),
        _node("review", "Review the result", "reviewer", "Check the completed work against the request. Return JSON with passed (boolean) and feedback (string).", {"request": "$.request", "work": "$.steps.review_execute.output"}, {"passed": "boolean", "feedback": "string"}),
        _node("finalize", "Present the result", "finalizer", "Present the result from the selected path clearly.", {"request": "$.request", "completed_steps": "$.steps"}),
    ],
    "edges": [
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
PRESETS = [
    {"id": "workflow-designer", "name": "AI Workflow Designer", "description": "Turn a plain-language brief into a reviewed, editable workflow JSON draft.", "definition": WORKFLOW_DESIGNER},
    {"id": "simple", "name": "Plan → Execute → Finalize", "description": "A straightforward workflow for general multi-step tasks.", "definition": SIMPLE},
    {"id": "reviewed", "name": "Plan → Execute → Review → Finalize", "description": "Adds a separate quality check before the result is presented.", "definition": REVIEWED},
    {"id": "adaptive", "name": "Adaptive answer or planned workflow", "description": "Answers simple requests in the routing call and routes multi-step work through planning and execution.", "definition": ADAPTIVE},
    {"id": "dynamic", "name": "Dynamic workflow", "description": "A routing agent answers simple requests in one call or chooses a planned or reviewed path for complex work. Use this for general-purpose requests.", "definition": DYNAMIC},
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
        else:
            allowed_output_types = {"string", "number", "integer", "boolean", "object", "array", "null"}
            for field, field_type in node.get("output_schema", {}).items():
                field_types = field_type if isinstance(field_type, list) else [field_type]
                if not field_types or any(not isinstance(item, str) or item not in allowed_output_types for item in field_types):
                    errors.append(
                        f"Node {node.get('id')} output_schema field {field!r} must use supported types; use [\"string\", \"null\"] for nullable text"
                    )
        if not isinstance(node.get("tools", []), list) or any(not isinstance(tool, str) for tool in node.get("tools", [])):
            errors.append(f"Node {node.get('id')} tools must be a list of names")
        if not isinstance(node.get("max_attempts", 1), int) or not 1 <= node.get("max_attempts", 1) <= 3:
            errors.append(f"Node {node.get('id')} max_attempts must be between 1 and 3")
        node_max_tokens = node.get("max_tokens")
        if node_max_tokens is not None and (
            isinstance(node_max_tokens, bool)
            or not isinstance(node_max_tokens, int)
            or not 1 <= node_max_tokens <= 200_000
        ):
            errors.append(f"Node {node.get('id')} max_tokens must be an integer between 1 and 200000 when specified")
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
