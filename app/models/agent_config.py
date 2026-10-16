from typing import Literal
from pydantic import BaseModel, Field, field_validator

class RoleModels(BaseModel):
    planner: str | None = None
    executor: str | None = None
    reviewer: str | None = None
    router: str | None = None
    direct: str | None = None
    controller: str | None = None
    finalizer: str | None = None

class Limits(BaseModel):
    max_input_chars: int = Field(default=30_000, ge=1, le=200_000)
    max_iterations: int = 5              # reviewer passes before giving up
    max_tokens_per_run: int = 50_000
    timeout_s: int = 120
    max_steps: int = 8                   # steps per plan
    max_parallel_steps: int = 3
    max_tool_turns: int = 4              # LLM<->tool rounds inside ONE step
    max_tool_output_chars: int = 4000    # larger tool results become artifacts
    llm_review: bool = True              # False = deterministic review only (faster)
    force_mode: Literal["direct", "plan"] | None = None

class AgentConfig(BaseModel):
    name: str
    provider_id: str
    workflow_version_id: str
    # Injected from the active WorkflowVersion at run time; not stored in agent config.
    workflow_definition: dict | None = Field(default=None, exclude=True)
    # Derived from workflow node tool assignments when a run starts.
    workflow_tool_names: list[str] = Field(default_factory=list, exclude=True)
    models: RoleModels
    # Step behavior and transitions are configured in the selected workflow.
    variables: list[str] = []            # declared runtime variable names
    use_platform_tools: bool = True      # future hook (section 10.6)
    knowledge_base_ids: list[str] = []
    temperature: float = 0.3
    # Approximate per-request prompt budget; actual capacity is also limited by the selected model.
    context_window_tokens: int | None = 4096
    limits: Limits = Limits()
    output_format: Literal["text"] = "text"   # "json" is a later feature

    @field_validator("context_window_tokens")
    @classmethod
    def _valid_context_window(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not 1024 <= value <= 200_000:
            raise ValueError("context_window_tokens must be between 1024 and 200000")
        return value

    @field_validator("models", mode="before")
    @classmethod
    def _shorthand(cls, v):
        # allow "models": "model-id"  ->  same model for all three roles
        return {"planner": v, "executor": v, "reviewer": v} if isinstance(v, str) else v
