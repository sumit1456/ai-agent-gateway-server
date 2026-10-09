from app.tools.registry import ToolRegistry
from app.engine.context import RunContext
from app.tools.base import ToolResult

async def call_tool(ctx: RunContext, tool_name: str, args: dict) -> ToolResult:
    """Call a tool via the registry, with error handling and caching."""
    # Check cache first
    from app.engine.retry import call_signature
    sig = call_signature(tool_name, args)
    cached = ctx.tool_cache.get(sig)
    if cached is not None:
        return cached
    
    # Call the tool
    result = await ctx.tools.call_tool(tool_name, args)
    
    # Cache successful results
    if result.ok:
        ctx.tool_cache[sig] = result
    
    return result