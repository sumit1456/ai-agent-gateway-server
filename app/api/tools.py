from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from app.security import current_user
from app.db import get_db
from app.models.tables import User, Tool
from typing import List, Optional
import uuid

router = APIRouter()

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
    
    return {
        "id": str(tool.id),
        "name": tool.name,
        "kind": tool.kind,
        "created_at": tool.created_at.isoformat()
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
            "created_at": tool.created_at.isoformat()
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
        "created_at": tool.created_at.isoformat()
    }

@router.put("/{tool_id}", response_model=dict)
async def update_tool(
    tool_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    auth_token: Optional[str] = None,
    input_schema: Optional[dict] = None,
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
    
    # Update fields if provided
    if name is not None:
        tool.name = name
    if description is not None:
        tool.description = description
    if endpoint_url is not None:
        tool.endpoint_url = endpoint_url
    if auth_token is not None:
        tool.auth_enc = auth_token  # TODO: Implement proper encryption
    if input_schema is not None:
        tool.input_schema = input_schema
    if timeout_ms is not None:
        tool.timeout_ms = timeout_ms
    
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    
    return {
        "id": str(tool.id),
        "name": tool.name,
        "updated_at": tool.created_at.isoformat()  # We don't have an updated_at field
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
    
    await db.delete(tool)
    await db.commit()
    
    return {
        "message": f"Tool {tool_id} deleted successfully"
    }

@router.post("/register", response_model=dict)
async def register_tool_alias(
    name: str,
    description: str,
    kind: str,
    endpoint_url: Optional[str] = None,
    auth_token: Optional[str] = None,
    input_schema: dict = {},
    timeout_ms: int = 10000,
    current_user: User = Depends(current_user),
    db: Depends(get_db) = Depends(get_db)
):
    """Alias for register_tool to match the architecture documentation."""
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