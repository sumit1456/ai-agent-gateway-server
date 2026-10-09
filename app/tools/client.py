from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.base import BaseTool, ToolResult
from app.engine.context import RunContext

class ClientToolInput(BaseModel):
    """Flexible input schema for client tools - accepts any kwargs."""
    class Config:
        extra = "allow"  # Allow any additional fields

    def __init__(self, **data):
        super().__init__(**data)

class ClientTool(BaseTool):
    def __init__(self, name: str, description: str):
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> type[BaseModel]:
        return ClientToolInput

    async def _execute(self, **kwargs) -> ToolResult:
        # For client tools, we don't execute them here - we return a special result
        # that indicates the client needs to execute it
        # In a real implementation, this would set up a future that the client can resolve
        import asyncio
        from app.engine.context import RunContext
        ctx: RunContext | None = None  # This would be set by the caller
        if ctx and hasattr(ctx, 'broker'):
            # Create a future for the client to resolve
            fut = ctx.broker.expect(f"client_tool:{self._name}")
            # Wait for the client to resolve it (with timeout)
            try:
                result = await asyncio.wait_for(fut, timeout=30.0)
                return ToolResult(ok=True, output=result)
            except asyncio.TimeoutError:
                return ToolResult(ok=False, error="Client tool execution timed out")
            except Exception as exc:
                return ToolResult(ok=False, error=f"Client tool execution failed: {exc}")
        else:
            # If we don't have a broker, we can't execute client tools
            return ToolResult(ok=False, error="Client tool broker not available")