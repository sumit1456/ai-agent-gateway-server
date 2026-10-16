from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.db import get_db
from app.engine.workflow_templates import PRESETS, get_preset, validate_definition
from app.models.tables import User, Workflow, WorkflowVersion, Agent, Run
from app.security import current_user

router = APIRouter()


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    definition: dict
    source_preset: str | None = None


class WorkflowUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    definition: dict


def _version_payload(workflow: Workflow, version: WorkflowVersion) -> dict:
    return {
        "id": str(workflow.id), "name": workflow.name,
        "description": workflow.description, "source_preset": workflow.source_preset,
        "version_id": str(version.id), "version": version.version,
        "definition": version.definition, "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


@router.get("/presets")
async def list_workflow_presets():
    return [{"id": p["id"], "name": p["name"], "description": p["description"], "definition": p["definition"]} for p in PRESETS]


@router.post("/validate")
async def validate_workflow(payload: WorkflowCreate, current_user: User = Depends(current_user)):
    errors = validate_definition(payload.definition)
    return {"valid": not errors, "errors": errors}


@router.get("/")
async def list_workflows(current_user: User = Depends(current_user), db=Depends(get_db)):
    result = await db.exec(select(Workflow).where(Workflow.user_id == current_user.id).order_by(Workflow.updated_at.desc()))
    workflows = result.all()
    output = []
    for workflow in workflows:
        version_result = await db.exec(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.is_active == True).order_by(WorkflowVersion.version.desc()).limit(1))
        version = version_result.first()
        if version:
            output.append(_version_payload(workflow, version))
    return output


@router.post("/")
async def create_workflow(payload: WorkflowCreate, current_user: User = Depends(current_user), db=Depends(get_db)):
    errors = validate_definition(payload.definition)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    workflow = Workflow(user_id=current_user.id, name=payload.name.strip(), description=payload.description, source_preset=payload.source_preset)
    db.add(workflow)
    await db.flush()
    version = WorkflowVersion(workflow_id=workflow.id, version=1, definition=payload.definition)
    db.add(version)
    await db.commit()
    await db.refresh(workflow)
    await db.refresh(version)
    return _version_payload(workflow, version)


@router.post("/from-preset/{preset_id}")
async def create_from_preset(preset_id: str, current_user: User = Depends(current_user), db=Depends(get_db)):
    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Workflow preset not found")
    workflow = Workflow(user_id=current_user.id, name=preset["name"], description=preset["description"], source_preset=preset_id)
    db.add(workflow)
    await db.flush()
    version = WorkflowVersion(workflow_id=workflow.id, version=1, definition=preset["definition"])
    db.add(version)
    await db.commit()
    await db.refresh(workflow)
    await db.refresh(version)
    return _version_payload(workflow, version)


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, payload: WorkflowUpdate, current_user: User = Depends(current_user), db=Depends(get_db)):
    try:
        workflow_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID")
    result = await db.exec(select(Workflow).where(Workflow.id == workflow_uuid, Workflow.user_id == current_user.id))
    workflow = result.first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    errors = validate_definition(payload.definition)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    versions = await db.exec(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id).order_by(WorkflowVersion.version.desc()).limit(1))
    previous = versions.first()
    if previous:
        previous.is_active = False
        db.add(previous)
        next_version = previous.version + 1
    else:
        next_version = 1
    version = WorkflowVersion(workflow_id=workflow.id, version=next_version, definition=payload.definition)
    workflow.name = payload.name.strip()
    workflow.description = payload.description
    workflow.updated_at = datetime.now(timezone.utc)
    db.add(workflow)
    db.add(version)
    await db.flush()
    if previous:
        linked_agents = await db.exec(select(Agent).where(Agent.workflow_version_id == previous.id))
        linked_agents = linked_agents.all()
        for agent in linked_agents:
            agent.workflow_version_id = version.id
            agent.config = {
                **(agent.config if isinstance(agent.config, dict) else {}),
                "workflow_version_id": str(version.id),
            }
            agent.config.pop("workflow_definition", None)
            agent.updated_at = datetime.now(timezone.utc)
            db.add(agent)
    await db.commit()
    if previous and linked_agents:
        from app.redis_client import invalidate_agent_prompt_config

        for agent in linked_agents:
            await invalidate_agent_prompt_config(str(current_user.id), str(agent.id))
    await db.refresh(version)
    return _version_payload(workflow, version)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, current_user: User = Depends(current_user), db=Depends(get_db)):
    try:
        workflow_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID")
    result = await db.exec(select(Workflow).where(Workflow.id == workflow_uuid, Workflow.user_id == current_user.id))
    workflow = result.first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    versions = await db.exec(select(WorkflowVersion.id).where(WorkflowVersion.workflow_id == workflow.id))
    version_ids = list(versions.all())
    linked_agents = []
    default_version = None
    if version_ids:
        linked = await db.exec(select(Agent).where(Agent.workflow_version_id.in_(version_ids)))
        linked_agents = linked.all()
        if linked_agents:
            default_result = await db.exec(
                select(Workflow)
                .where(Workflow.user_id == current_user.id, Workflow.source_preset == "dynamic", Workflow.id != workflow.id)
                .order_by(Workflow.updated_at.desc())
            )
            default_workflow = default_result.first()
            if default_workflow:
                active_result = await db.exec(
                    select(WorkflowVersion)
                    .where(WorkflowVersion.workflow_id == default_workflow.id, WorkflowVersion.is_active == True)
                    .order_by(WorkflowVersion.version.desc()).limit(1)
                )
                default_version = active_result.first()
            if not default_version:
                preset = get_preset("dynamic")
                if not preset:
                    raise HTTPException(status_code=500, detail="The default Dynamic workflow is unavailable")
                if not default_workflow:
                    default_workflow = Workflow(
                        user_id=current_user.id,
                        name=preset["name"],
                        description=preset["description"],
                        source_preset="dynamic",
                    )
                    db.add(default_workflow)
                    await db.flush()
                version_result = await db.exec(
                    select(WorkflowVersion.version)
                    .where(WorkflowVersion.workflow_id == default_workflow.id)
                    .order_by(WorkflowVersion.version.desc()).limit(1)
                )
                previous_version = version_result.first() or 0
                default_version = WorkflowVersion(
                    workflow_id=default_workflow.id,
                    version=previous_version + 1,
                    definition=preset["definition"],
                )
                db.add(default_version)
                await db.flush()

            for agent in linked_agents:
                agent.workflow_version_id = default_version.id
                agent.config = {
                    **(agent.config if isinstance(agent.config, dict) else {}),
                    "workflow_version_id": str(default_version.id),
                }
                agent.config.pop("workflow_definition", None)
                agent.updated_at = datetime.now(timezone.utc)
                db.add(agent)

        # Keep run records and step traces intact while releasing the version
        # foreign key so the workflow and its versions can be deleted safely.
        version_rows = await db.exec(select(WorkflowVersion).where(WorkflowVersion.id.in_(version_ids)))
        version_map = {row.id: row for row in version_rows.all()}
        history = await db.exec(select(Run).where(Run.workflow_version_id.in_(version_ids)))
        for run in history.all():
            version = version_map.get(run.workflow_version_id)
            state = dict(run.state or {})
            if version:
                state["workflow_snapshot"] = {
                    "workflow_id": str(workflow.id),
                    "workflow_name": workflow.name,
                    "version": version.version,
                    "definition": version.definition,
                }
            run.state = state
            run.workflow_version_id = None
            db.add(run)
    await db.delete(workflow)
    await db.commit()
    if linked_agents:
        from app.redis_client import invalidate_agent_prompt_config

        for agent in linked_agents:
            await invalidate_agent_prompt_config(str(current_user.id), str(agent.id))
    return {
        "ok": True,
        "agents_reassigned": len(linked_agents),
        "default_workflow_id": str(default_version.workflow_id) if default_version else None,
        "default_workflow_name": default_workflow.name if default_version else None,
    }
