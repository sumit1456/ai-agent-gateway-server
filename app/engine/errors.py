"""Custom exceptions for the agent engine."""

class EngineError(Exception):
    """Base exception for all engine errors."""
    pass

class ProviderError(EngineError):
    """LLM provider API error (rate limit, auth, etc)."""
    pass

class ToolError(EngineError):
    """Tool execution error."""
    pass

class TimeoutError(EngineError):
    """Run exceeded timeout limit."""
    pass

class BudgetError(EngineError):
    """Run exceeded token budget."""
    pass

class ValidationError(EngineError):
    """Invalid plan or configuration."""
    pass
