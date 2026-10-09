from __future__ import annotations
import httpx
from typing import Dict, Any
from pydantic import BaseModel, Field
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
                 auth_enc: str | None = None, timeout_ms: int = 10000):
        self._name = name
        self._description = description
        self.endpoint_url = endpoint_url
        self.auth_enc = auth_enc
        self.timeout_ms = timeout_ms

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> type[BaseModel]:
        return WebhookToolInput

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