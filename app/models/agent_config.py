from typing import Literal
from pydantic import BaseModel, field_validator

class RoleModels(BaseModel):
    planner: str
    executor: str
    reviewer: str

class Limits(BaseModel):
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
    models: RoleModels
    system_prompt: str                   # Jinja2 template (the agent's persona/rules)
    
    # Customizable System Prompts (optional - uses defaults if not provided)
    router_prompt: str | None = None     # Custom router decision prompt
    planner_prompt: str | None = None    # Custom planning prompt
    executor_prompt: str | None = None   # Custom execution prompt
    reviewer_prompt: str | None = None   # Custom review prompt
    direct_prompt: str | None = None     # Custom direct mode prompt
    finalizer_prompt: str | None = None  # Custom answer finalization prompt
    
    variables: list[str] = []            # declared runtime variable names
    tools: list[str] = []                # names of the user's registered tools
    use_platform_tools: bool = True      # future hook (section 10.6)
    knowledge_base_ids: list[str] = []
    temperature: float = 0.3
    limits: Limits = Limits()
    output_format: Literal["text"] = "text"   # "json" is a later feature

    @field_validator("models", mode="before")
    @classmethod
    def _shorthand(cls, v):
        # allow "models": "model-id"  ->  same model for all three roles
        return {"planner": v, "executor": v, "reviewer": v} if isinstance(v, str) else v