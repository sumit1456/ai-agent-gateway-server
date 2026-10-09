from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Any
from langchain_core.tools import StructuredTool

class ToolResult(BaseModel):
    ok: bool
    output: Any = None
    error: str | None = None

    @classmethod
    def from_exception(cls, exc: Exception) -> "ToolResult":
        return cls(ok=False, error=str(exc))

class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def input_schema(self) -> type[BaseModel] | None:
        """Return the Pydantic model for tool input. Override in subclasses."""
        return None

    @abstractmethod
    async def _execute(self, **kwargs) -> ToolResult: ...

    async def execute(self, **kwargs) -> ToolResult:
        try:
            return await self._execute(**kwargs)
        except Exception as exc:
            return ToolResult.from_exception(exc)

    def to_langchain(self) -> StructuredTool:
        """Convert this tool to a LangChain StructuredTool."""
        import asyncio

        def sync_execute(**kwargs):
            """Synchronous wrapper for async execute."""
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(self.execute(**kwargs))
                if result.ok:
                    return result.output
                else:
                    return f"Error: {result.error}"
            finally:
                loop.close()

        async def async_execute(**kwargs):
            """Async wrapper for execute."""
            result = await self.execute(**kwargs)
            if result.ok:
                return result.output
            else:
                return f"Error: {result.error}"

        return StructuredTool(
            name=self.name,
            description=self.description,
            func=sync_execute,
            coroutine=async_execute,
            args_schema=self.input_schema,
        )