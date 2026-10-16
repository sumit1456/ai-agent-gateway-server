from typing import Literal
from pydantic import BaseModel, Field, field_validator

class RoleModels(BaseModel):
    planner: str
    executor: str
    reviewer: str

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
    workflow_definition: dict | None = None
    execution_style: Literal["graph_workflow", "single_orchestration"] = "graph_workflow"
    models: RoleModels
    system_prompt: str                   # Jinja2 template (the agent's persona/rules)
    
    # Customizable System Prompts (optional - uses defaults if not provided)
    router_prompt: str | None = None     # Custom router decision prompt
    planner_prompt: str | None = None    # Custom planning prompt
    executor_prompt: str | None = None   # Custom execution prompt
    reviewer_prompt: str | None = None   # Custom review prompt
    direct_prompt: str | None = None     # Custom direct mode prompt
    finalizer_prompt: str | None = None  # Custom answer finalization prompt
    controller_prompt: str | None = None  # Advanced controller prompt override

    # Preset choices and additive guidance are stored inside agents.config JSON.
    role_prompt_presets: dict[str, str] = Field(default_factory=dict)
    role_instructions: dict[str, str] = Field(default_factory=dict)
    
    variables: list[str] = []            # declared runtime variable names
    tools: list[str] = []                # names of the user's registered tools
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
