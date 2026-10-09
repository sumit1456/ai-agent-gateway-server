from __future__ import annotations
from typing import Literal, Any, TypedDict
from pydantic import BaseModel, Field

# What kind of content an artifact holds.
# step_output  – default; raw executor output passed between steps
# code         – source code (any language)
# document     – formatted prose / report / markdown
# data         – structured data (JSON, CSV, table)
# error_log    – failure details from a step
# final_answer – the composed user-facing answer
ArtifactKind = Literal["step_output", "code", "document", "data", "error_log", "final_answer"]

StopReason = Literal["done", "max_iterations", "budget", "timeout", "cancelled", "unrecoverable"]

class StopRun(Exception):
    """Raised inside the engine to end the run early with a stop reason."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

# Pydantic model for validation
class StepModel(BaseModel):
    """Pydantic model for Step validation and serialization."""
    id: str
    goal: str
    tools: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    kb_queries: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    status: Literal["pending", "running", "done", "failed"] = "pending"
    attempts: int = 0
    result_summary: str | None = None
    artifact_id: str | None = None
    problems: list[str] = Field(default_factory=list)
    
    class Config:
        extra = "forbid"

# TypedDict for state (used by LangGraph)
class Step(TypedDict, total=False):
    """Step as used in LangGraph state (dict-based)."""
    id: str
    goal: str
    tools: list[str]
    inputs: list[str]
    kb_queries: list[str]
    depends_on: list[str]
    success_criteria: str
    status: str  # "pending" | "running" | "done" | "failed"
    attempts: int
    result_summary: str | None
    artifact_id: str | None
    problems: list[str]

class RunState(TypedDict, total=False):
    """Represents the complete state of an agent run (dict-based for LangGraph)."""
    run_id: str
    input: str
    variables: dict[str, Any]
    mode: str  # "direct" | "plan"
    goal: str
    steps: list[Step]
    facts: list[str]
    artifacts: dict[str, dict]
    dead_ends: list[str]
    iteration: int
    last_wave: list[str]
    pending_replan: bool
    replan_reason: str
    plan_hashes: list[str]
    stop_reason: str | None
    answer: str

# ---- schemas the LLM must produce ----
class RouteDecision(BaseModel):
    mode: Literal["direct", "plan"]
    reason: str = ""

class PlanStep(BaseModel):
    id: str
    goal: str
    tools: list[str] = []
    inputs: list[str] = []
    kb_queries: list[str] = []
    depends_on: list[str] = []
    success_criteria: str = ""

class Plan(BaseModel):
    steps: list[PlanStep]

class StepResult(BaseModel):
    status: Literal["done", "failed"]
    summary: str                # <= 2 sentences
    output: str = ""            # full result, stored as an artifact
    kind: ArtifactKind = "step_output"  # what type of content is in output
    facts: list[str] = []
    problems: list[str] = []

class StepVerdict(BaseModel):
    step_id: str
    verdict: Literal["ok", "retry_step", "replan", "abort"]
    reason: str = ""

class ReviewResult(BaseModel):
    verdicts: list[StepVerdict]