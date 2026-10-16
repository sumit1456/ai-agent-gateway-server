from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select
from app.security import current_user
from app.db import get_db
from app.models.tables import User, Tool, Agent, Workflow, WorkflowVersion
from typing import List, Optional
from copy import deepcopy
from datetime import datetime, timezone
import uuid

router = APIRouter()


class ToolTestRequest(BaseModel):
    endpoint_url: str
    input_schema: dict = Field(default_factory=dict)
    arguments: dict = Field(default_factory=dict)
    timeout_ms: int = 10000
    auth_token: Optional[str] = None
    tool_id: Optional[str] = None


@router.post("/test", response_model=dict)
async def test_tool(
    payload: ToolTestRequest,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db),
):
    """Validate sample arguments against the runtime schema and call the webhook."""
    if not 1 <= payload.timeout_ms <= 120000:
        raise HTTPException(status_code=400, detail="Timeout must be between 1 and 120000 milliseconds")
    if not payload.endpoint_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Endpoint URL must use HTTP or HTTPS")

    saved_tool = None
    if payload.tool_id:
        try:
            tool_uuid = uuid.UUID(payload.tool_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid tool ID format") from exc
        result = await db.exec(select(Tool).where(Tool.id == tool_uuid, Tool.user_id == current_user.id))
        saved_tool = result.first()
        if not saved_tool:
            raise HTTPException(status_code=404, detail="Tool not found")

    from app.tools.webhook import WebhookTool

    try:
        tool = WebhookTool(
            name="tool_test",
            description="Temporary webhook test",
            endpoint_url=payload.endpoint_url,
            auth_enc=payload.auth_token or (saved_tool.auth_enc if saved_tool else None),
            timeout_ms=payload.timeout_ms,
            input_schema=payload.input_schema,
        )
        validated = tool.input_schema.model_validate(payload.arguments)
    except (TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Arguments do not match the tool schema: {exc}") from exc

    arguments = validated.model_dump(exclude_unset=True)
    result = await tool.execute(**arguments)
    return {
        "ok": result.ok,
        "arguments_sent": arguments,
        "output": result.output,
        "error": result.error,
    }


def _without_tool(definition: dict, tool_name: str) -> tuple[dict, bool]:
    """Return a workflow definition with the deleted tool removed from node lists."""
    cleaned = deepcopy(definition)
    changed = False
    for node in cleaned.get("nodes", []):
        node_tools = node.get("tools")
        if isinstance(node_tools, list) and tool_name in node_tools:
            node["tools"] = [name for name in node_tools if name != tool_name]
            changed = True
    return cleaned, changed


def _rename_tool(definition: dict, old_name: str, new_name: str) -> tuple[dict, bool]:
    cleaned = deepcopy(definition)
    changed = False
    for node in cleaned.get("nodes", []):
        node_tools = node.get("tools")
        if isinstance(node_tools, list) and old_name in node_tools:
            node["tools"] = [new_name if name == old_name else name for name in node_tools]
            changed = True
    return cleaned, changed


def _agent_config_without_tool(config: dict, tool_name: str) -> tuple[dict, bool]:
    cleaned = deepcopy(config or {})
    changed = False
    configured_tools = cleaned.get("tools")
    if isinstance(configured_tools, list) and tool_name in configured_tools:
        cleaned["tools"] = [name for name in configured_tools if name != tool_name]
        changed = True
    if "workflow_definition" in cleaned:
        cleaned.pop("workflow_definition")
        changed = True
    return cleaned, changed


def _agent_config_renamed_tool(config: dict, old_name: str, new_name: str) -> tuple[dict, bool]:
    cleaned = deepcopy(config or {})
    changed = False
    configured_tools = cleaned.get("tools")
    if isinstance(configured_tools, list) and old_name in configured_tools:
        cleaned["tools"] = [new_name if name == old_name else name for name in configured_tools]
        changed = True
    if "workflow_definition" in cleaned:
        cleaned.pop("workflow_definition")
        changed = True
    return cleaned, changed

@router.post("/", response_model=dict)
async def register_tool(
    name: str,
    description: str,
    kind: str,  # "webhook" or "client"
    endpoint_url: Optional[str] = None,
    auth_token: Optional[str] = None,  # In practice, this would be encrypted
    input_schema: dict = {},
    timeout_ms: int = 10000,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Register a new tool for the current user."""
    if kind not in ["webhook", "client"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tool kind must be 'webhook' or 'client'"
        )
    
    if kind == "webhook" and not endpoint_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook tools require an endpoint_url"
        )

    if kind == "webhook":
        from app.tools.webhook import WebhookTool
        try:
            WebhookTool._build_input_schema(input_schema)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid tool input schema: {exc}") from exc
    
    # Check if tool already exists for this user
    result = await db.exec(
        select(Tool).where(
            Tool.user_id == current_user.id,
            Tool.name == name
        )
    )
    existing = result.first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tool '{name}' already exists"
        )
    
    # In a real implementation, we would encrypt the auth token
    # For now, we'll store it as-is (not secure for production)
    auth_enc = auth_token  # TODO: Implement proper encryption
    
    tool = Tool(
        user_id=current_user.id,
        name=name,
        description=description,
        kind=kind,
        endpoint_url=endpoint_url,
        auth_enc=auth_enc,
        input_schema=input_schema,
        timeout_ms=timeout_ms
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    from app.redis_client import invalidate_agent_execution_configs
    await invalidate_agent_execution_configs(str(current_user.id))
    
    return {
        "id": str(tool.id),
        "name": tool.name,
        "kind": tool.kind,
    }

@router.get("/", response_model=List[dict])
async def list_tools(
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """List all tools for the current user."""
    result = await db.exec(
        select(Tool).where(Tool.user_id == current_user.id)
    )
    tools = result.all()
    
    return [
        {
            "id": str(tool.id),
            "name": tool.name,
            "description": tool.description,
            "kind": tool.kind,
            "endpoint_url": tool.endpoint_url,
            "timeout_ms": tool.timeout_ms,
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]

@router.get("/{tool_id}", response_model=dict)
async def get_tool(
    tool_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Get a specific tool."""
    try:
        tool_uuid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tool ID format"
        )
    
    result = await db.exec(
        select(Tool).where(
            Tool.user_id == current_user.id,
            Tool.id == tool_uuid
        )
    )
    tool = result.first()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    
    return {
        "id": str(tool.id),
        "name": tool.name,
        "description": tool.description,
        "kind": tool.kind,
        "endpoint_url": tool.endpoint_url,
        "auth_enc": tool.auth_enc,  # In practice, we wouldn't return this
        "input_schema": tool.input_schema,
        "timeout_ms": tool.timeout_ms,
    }

@router.put("/{tool_id}", response_model=dict)
async def update_tool(
    tool_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    auth_token: Optional[str] = None,
    payload: Optional[dict] = Body(default=None),
    timeout_ms: Optional[int] = None,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Update a tool."""
    try:
        tool_uuid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tool ID format"
        )
    
    result = await db.exec(
        select(Tool).where(
            Tool.user_id == current_user.id,
            Tool.id == tool_uuid
        )
    )
    tool = result.first()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    
    input_schema = None
    body_auth_token = None
    if payload is not None:
        if isinstance(payload, dict) and isinstance(payload.get("input_schema"), dict):
            input_schema = payload.get("input_schema")
            body_auth_token = payload.get("auth_token")
        elif isinstance(payload, dict):
            # Keep accepting the original raw JSON Schema request body.
            input_schema = payload

    if input_schema is not None:
        from app.tools.webhook import WebhookTool
        try:
            WebhookTool._build_input_schema(input_schema)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid tool input schema: {exc}") from exc

    old_name = tool.name
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Tool name cannot be empty")
        existing_result = await db.exec(select(Tool).where(
            Tool.user_id == current_user.id, Tool.name == name, Tool.id != tool.id
        ))
        if existing_result.first():
            raise HTTPException(status_code=400, detail=f"Tool '{name}' already exists")

    affected_agents = []
    if name is not None and name != old_name:
        workflow_result = await db.exec(
            select(Workflow, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(Workflow.user_id == current_user.id, WorkflowVersion.is_active == True)
        )
        successors = {}
        for workflow, active_version in workflow_result.all():
            renamed, changed = _rename_tool(active_version.definition or {}, old_name, name)
            if not changed:
                continue
            active_version.is_active = False
            successor = WorkflowVersion(workflow_id=workflow.id, version=active_version.version + 1, definition=renamed)
            workflow.updated_at = datetime.now(timezone.utc)
            db.add(active_version)
            db.add(successor)
            db.add(workflow)
            await db.flush()
            successors[workflow.id] = successor

        if successors:
            versions_result = await db.exec(select(WorkflowVersion).where(WorkflowVersion.workflow_id.in_(successors)))
            successor_by_version = {
                version.id: successors[version.workflow_id]
                for version in versions_result.all()
            }
        else:
            successor_by_version = {}
        agents_result = await db.exec(select(Agent).where(Agent.user_id == current_user.id))
        for agent in agents_result.all():
            updated_config, changed = _agent_config_renamed_tool(agent.config, old_name, name)
            successor = successor_by_version.get(agent.workflow_version_id)
            if successor:
                agent.workflow_version_id = successor.id
                updated_config["workflow_version_id"] = str(successor.id)
                changed = True
            if changed:
                agent.config = updated_config
                agent.updated_at = datetime.now(timezone.utc)
                db.add(agent)
                affected_agents.append(agent)

    # Update fields if provided
    if name is not None:
        tool.name = name
    if description is not None:
        tool.description = description
    if endpoint_url is not None:
        tool.endpoint_url = endpoint_url
    if body_auth_token is not None:
        tool.auth_enc = body_auth_token  # TODO: Implement proper encryption
    elif auth_token is not None:
        tool.auth_enc = auth_token  # Backward compatibility for query-based clients.
    if input_schema is not None:
        tool.input_schema = input_schema
    if timeout_ms is not None:
        tool.timeout_ms = timeout_ms
    
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    from app.redis_client import invalidate_agent_execution_configs, invalidate_agent_prompt_config
    for agent in affected_agents:
        await invalidate_agent_prompt_config(str(current_user.id), str(agent.id))
    await invalidate_agent_execution_configs(str(current_user.id))
    
    return {
        "id": str(tool.id),
        "name": tool.name
    }

@router.delete("/{tool_id}", response_model=dict)
async def delete_tool(
    tool_id: str,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Delete a tool."""
    try:
        tool_uuid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tool ID format"
        )
    
    result = await db.exec(
        select(Tool).where(
            Tool.user_id == current_user.id,
            Tool.id == tool_uuid
        )
    )
    tool = result.first()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    
    # Create new versions for active workflows that reference the deleted tool.
    workflow_result = await db.exec(
        select(Workflow, WorkflowVersion)
        .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
        .where(Workflow.user_id == current_user.id, WorkflowVersion.is_active == True)
    )
    affected_workflows: dict[uuid.UUID, tuple[Workflow, WorkflowVersion, WorkflowVersion]] = {}
    for workflow, active_version in workflow_result.all():
        cleaned_definition, changed = _without_tool(active_version.definition or {}, tool.name)
        if not changed:
            continue
        active_version.is_active = False
        db.add(active_version)
        next_version = WorkflowVersion(
            workflow_id=workflow.id,
            version=active_version.version + 1,
            definition=cleaned_definition,
        )
        workflow.updated_at = datetime.now(timezone.utc)
        db.add(workflow)
        db.add(next_version)
        await db.flush()
        affected_workflows[workflow.id] = (workflow, active_version, next_version)

    agents_result = await db.exec(select(Agent).where(Agent.user_id == current_user.id))
    agents = agents_result.all()
    affected_agents: list[Agent] = []
    affected_workflow_ids = set(affected_workflows)
    version_to_successor: dict[uuid.UUID, WorkflowVersion] = {}
    if affected_workflow_ids:
        version_result = await db.exec(
            select(WorkflowVersion).where(WorkflowVersion.workflow_id.in_(affected_workflow_ids))
        )
        workflow_id_by_version = {version.id: version.workflow_id for version in version_result.all()}
        for workflow_id, (_, _, successor) in affected_workflows.items():
            for version_id, linked_workflow_id in workflow_id_by_version.items():
                if linked_workflow_id == workflow_id:
                    version_to_successor[version_id] = successor

    for agent in agents:
        updated_config, config_changed = _agent_config_without_tool(agent.config, tool.name)
        successor = version_to_successor.get(agent.workflow_version_id)
        if successor:
            agent.workflow_version_id = successor.id
            updated_config["workflow_version_id"] = str(successor.id)
            config_changed = True
        if config_changed:
            agent.config = updated_config
            agent.updated_at = datetime.now(timezone.utc)
            db.add(agent)
            affected_agents.append(agent)

    await db.delete(tool)
    await db.commit()
    from app.redis_client import invalidate_agent_execution_configs, invalidate_agent_prompt_config
    for agent in affected_agents:
        await invalidate_agent_prompt_config(str(current_user.id), str(agent.id))
    await invalidate_agent_execution_configs(str(current_user.id))
    
    return {
        "message": f"Tool {tool_id} deleted successfully",
        "agents_updated": len(affected_agents),
        "workflows_updated": len(affected_workflows),
    }

@router.post("/register", response_model=dict)
async def register_tool_alias(
    name: str,
    description: str,
    kind: str,
    endpoint_url: Optional[str] = None,
    payload: dict = Body(default={}),
    timeout_ms: int = 10000,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Alias for register_tool to match the architecture documentation."""
    # Accept the original raw schema body as well as the newer wrapped form.
    if isinstance(payload, dict) and isinstance(payload.get("input_schema"), dict):
        input_schema = payload.get("input_schema") or {}
        auth_token = payload.get("auth_token")
    else:
        input_schema = payload if isinstance(payload, dict) else {}
        auth_token = None
    return await register_tool(
        name=name,
        description=description,
        kind=kind,
        endpoint_url=endpoint_url,
        auth_token=auth_token,
        input_schema=input_schema,
        timeout_ms=timeout_ms,
        current_user=current_user,
        db=db
    )
