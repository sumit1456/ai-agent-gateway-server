from __future__ import annotations
import httpx
from typing import Any
from pydantic import BaseModel, Field, create_model
from app.tools.base import BaseTool, ToolResult
from app.engine.context import RunContext

class WebhookToolInput(BaseModel):
    """Flexible input schema for webhook tools - accepts any kwargs."""
    class Config:
        extra = "allow"  # Allow any additional fields

    def __init__(self, **data):
        super().__init__(**data)

class WebhookTool(BaseTool):
    def __init__(self, name: str, description: str, endpoint_url: str, 
                 auth_enc: str | None = None, timeout_ms: int = 10000,
                 input_schema: dict | None = None):
        self._name = name
        self._description = description
        self.endpoint_url = endpoint_url
        self.auth_enc = auth_enc
        self.timeout_ms = timeout_ms
        self._input_schema = self._build_input_schema(input_schema or {})

    @staticmethod
    def _build_input_schema(schema: dict) -> type[BaseModel]:
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        type_map = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list}
        fields = {}
        for name, definition in properties.items():
            definition = definition if isinstance(definition, dict) else {}
            field_type = type_map.get(definition.get("type"), Any)
            default = ... if name in required else None
            fields[name] = (field_type, Field(default, description=definition.get("description", "")))
        return create_model("WebhookToolInput", **fields) if fields else WebhookToolInput

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> type[BaseModel]:
        return self._input_schema

    async def _execute(self, **kwargs) -> ToolResult:
        headers = {}
        if self.auth_enc:
            # In a real implementation, we would decrypt this
            # For now, we'll assume it's already decrypted or handled elsewhere
            headers["Authorization"] = self.auth_enc
        
        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
            try:
                response = await client.post(self.endpoint_url, json=kwargs, headers=headers)
                response.raise_for_status()
                return ToolResult(ok=True, output=response.json())
            except Exception as exc:
                return ToolResult.from_exception(exc)
