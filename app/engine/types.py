from __future__ import annotations
from typing import Literal, TypedDict
from pydantic import BaseModel

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

class Step(TypedDict, total=False):
    id: str
    goal: str
    tools: list[str]            # tool names this step may use
    inputs: list[str]           # ids of EARLIER STEPS whose output this step needs
    kb_queries: list[str]       # retrieval queries to run before the step
    depends_on: list[str]       # step ids that must be "done" first (superset of inputs)
    success_criteria: str
    status: str                 # pending | running | done | failed
    attempts: int               # number of RETRIES so far (0 = first attempt)
    result_summary: str | None
    artifact_id: str | None     # artifact holding the step's full output
    problems: list[str]

class RunState(TypedDict, total=False):
    run_id: str
    input: str
    variables: dict
    mode: str                   # "direct" | "plan"
    goal: str
    steps: list[Step]
    facts: list[str]            # short, deduped, durable findings (<= 20)
    artifacts: dict[str, dict]  # id -> {kind, summary, size}   (handles only)
    dead_ends: list[str]        # notes on approaches that failed (fed to the planner)
    iteration: int              # reviewer passes so far
    last_wave: list[str]        # step ids executed in the latest executor pass
    pending_replan: bool
    replan_reason: str
    plan_hashes: list[str]      # to detect an identical replan (no progress)
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