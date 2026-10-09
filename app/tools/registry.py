from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.engine.context import RunContext

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def describe(self, exclude: Optional[set[str]] = None) -> list[str]:
        exclude = exclude or set()
        return [f"{name}: {tool.description}" for name, tool in self._tools.items() if name not in exclude]

    def schemas(self, tool_names: list[str]) -> list:
        """Get LangChain tool schemas for the given tool names."""
        from langchain_core.tools import StructuredTool
        schemas = []
        for name in tool_names:
            tool = self.get(name)
            if tool:
                schemas.append(tool.to_langchain())
        return schemas

    async def call_tool(self, name: str, args: dict) -> ToolResult:
        """Call a tool by name with the given arguments."""
        tool = self.get(name)
        if not tool:
            return ToolResult(ok=False, error=f"Unknown tool: {name}")
        return await tool.execute(**args)