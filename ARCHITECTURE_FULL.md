# Agent Gateway: Full Architecture & Implementation Guide

> **Product goal:** A developer registers, configures an agent once, and calls one endpoint.
> The gateway owns every agent internal: routing, planning, execution, review, retries, context, and RAG.
>
> **Audience:** any developer or LLM implementing this. It is organized file by file, with code you can copy.
> Follow the build order in section 16 and check each "Done when" before moving on.

**Stack:** Python 3.11+ · FastAPI · LangGraph + LangChain model wrappers · PostgreSQL (SQLModel) · Pinecone · NVIDIA NIM / OpenRouter

---

## 0. Rules for the Implementer

1. **Use the exact file paths, function names, and signatures in this doc.** Other files import them.
2. **All LLM calls go through `app/engine/llm_utils.py`** (`structured_call`, `llm_turn`). Nodes call them as `llm.structured_call(...)` via a *module reference* (`from app.engine import llm_utils as llm`), never `from ... import structured_call`. Tests rely on monkeypatching the module attribute.
3. **Nodes never mutate the incoming `state`.** Copy lists and dicts, then return only the keys that changed.
4. **Do not invent third-party APIs.** If a call below fails, read the installed package (`python -c "import pkg; help(pkg.X)"`) rather than guessing. SDKs for LangGraph, LangChain, and Pinecone change often.
5. **Do not add features that are not listed.** If something is unclear, pick the simplest option and leave a `# TODO(decision): ...` comment.
6. **After the first successful install, run `pip freeze > requirements.lock`.** The `requirements.txt` below is unpinned on purpose.
7. **Model IDs differ per provider** (OpenRouter: `meta-llama/llama-3.1-70b-instruct`, NVIDIA: `meta/llama-3.1-70b-instruct`). Model names in this doc are examples. Verify them against the provider's model list.
8. **MVP is single-process.** Run registries, the client-tool broker, and artifacts live in memory (section 18). Run one uvicorn worker.

---

## 1. Goal & Scope

**In scope**
- Register, add a provider key (BYOK), create an agent config, call the agent (SSE stream or poll).
- Agent engine: Router → Planner → Executor → Reviewer, with a retry policy, stop reasons, parallel steps, and per-role models.
- Three context layers: Run State, Artifacts, Knowledge (Pinecone).
- User-registered tools: `webhook` (gateway calls the user's URL) and `client` (executed by the calling app).
- Per-run metrics and a small golden test set to measure agent performance.

**Out of scope (parked)**
- Production hardening: SSRF protection, rate limits, multi-tenant isolation, quotas, billing.
- Durable run resume, multi-agent, JSON-schema output mode, OAuth login.
- Platform-provided tools (hook is designed in section 10.6, implementation is later).

---

## 2. Stack & Install

`requirements.txt`
```
fastapi
uvicorn[standard]
pydantic>=2
pydantic-settings
sqlmodel
sqlalchemy[asyncio]
asyncpg
alembic
langgraph
langchain-core
langchain-openai
langchain-text-splitters
openai
httpx
jinja2
jsonschema
cryptography
bcrypt
pinecone
pypdf
python-multipart
pyyaml
pytest
pytest-asyncio
```

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip freeze > requirements.lock
```

---

## 3. System Overview

```
 Client app / UI
      |  Bearer API key . input + variables
      v
 +--------------------------- GATEWAY (FastAPI) ---------------------------+
 |  Auth . Providers . Agents . Tools . Knowledge bases . Runs (REST + SSE) |
 |                                                                          |
 |  +------------------- AGENT ENGINE (LangGraph) ----------------------+  |
 |  |  Router --direct--> Direct pass                                    |  |
 |  |     |plan                                                          |  |
 |  |     v                                                              |  |
 |  |  Planner -> Executor <-> Tools -> Reviewer -> Finalize             |  |
 |  |     ^______ replan _______________|                                |  |
 |  |  Context layers:  [Run State]  [Artifacts]  [Knowledge]           |  |
 |  +--------------------------------------------------------------------+  |
 |  Tool registry: user tools (webhook | client) . built-ins               |
 +------------------+--------------------------------+---------------------+
               PostgreSQL                          Pinecone
   (users, agents, runs, artifacts, KB meta)     (knowledge vectors)
```

### 3.1 Request lifecycle

1. `POST /v1/agents/{id}/run` with JSON body `{ "input": "...", "variables": {...} }`.
2. API loads the agent config, decrypts the provider key, resolves tools, and builds a `RunContext`.
3. `execute_run` invokes the LangGraph graph in a background task. Nodes emit events into the context's `EventSink`.
4. The SSE response drains the sink. For `stream: false`, the client polls `GET /v1/runs/{id}`.
5. At the end, the run, artifacts, and new session facts are persisted, and `run.done` is emitted.

### 3.2 Model roles

| Role | Used by | Model should be |
|---|---|---|
| `planner` | Planner node | Strong reasoning |
| `executor` | Direct node, step execution, finalize | Reliable tool calling |
| `reviewer` | Router, reviewer | Cheap and fast |

### 3.3 Three context layers

| Layer | Contents | Access |
|---|---|---|
| **Run State** | Goal, todo steps and status, short results, facts, dead ends | Pushed into prompts as a small per-role view (`build_view`) |
| **Artifacts** | Bulky outputs: step outputs, large tool results | Stored by id (`a1`, `a2`). State holds only handle + summary. Agents pull via `read_artifact` |
| **Knowledge** | Domain docs in Pinecone | Retrieved by query: planner-declared `kb_queries` before a step, or the `kb_search` tool |

Run State is small and structured, so it uses plain lookups. RAG is only for the knowledge layer.

---

## 4. Repo Layout

```
agent-gateway/
  app/
    main.py
    config.py
    db.py
    security.py
    models/        tables.py  agent_config.py
    providers/     base.py  openrouter.py  nvidia.py  registry.py
    engine/
      types.py         state types + LLM output schemas + StopRun
      context.py       RunContext, EventSink, Usage, ArtifactStore, ClientToolBroker, RUNS
      retry.py         classify errors, retry_async, signatures
      llm_utils.py     structured_call, llm_turn   (ONLY place that calls LLMs)
      prompts.py
      views.py         build_view per role
      state_ops.py     validate_plan, apply_result, ready_steps, merge_facts
      tool_loop.py     run_tool_loop, run_step, prefetch_kb
      nodes.py         router, direct, planner, executor, reviewer, finalize
      graph.py
      runner.py        build_context, run_graph, execute_run
    tools/         base.py  registry.py  call.py  webhook.py  client.py  builtin.py
    knowledge/     embedder.py  retriever.py  ingest.py  factory.py
    api/           auth.py  providers.py  agents.py  tools.py  knowledge.py  runs.py
  tests/           __init__.py  conftest.py  test_state_ops.py  test_tools.py  test_flow.py
  pytest.ini
  scripts/         ping_llm.py  run_local.py  run_golden.py
  golden/          tasks.yaml
  docker-compose.yml
  .env.example
  requirements.txt
```

**Import rule:** `tools/*` and `engine/context.py` reference each other only for type hints. Use `from __future__ import annotations` and `if TYPE_CHECKING:` imports to avoid cycles.

---

## 5. Config & Environment

`.env.example`
```ini
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_gateway
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=
PINECONE_API_KEY=
PINECONE_INDEX_HOST=
RERANK_MODEL=bge-reranker-v2-m3
EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5
EMBEDDING_DIM=1024
LOG_LEVEL=INFO
```

`app/config.py`
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_gateway"
    encryption_key: str
    pinecone_api_key: str = ""
    pinecone_index_host: str = ""
    rerank_model: str = "bge-reranker-v2-m3"
    embedding_model: str = "nvidia/nv-embedqa-e5-v5"
    embedding_dim: int = 1024
    log_level: str = "INFO"

settings = Settings()
```

The Pinecone index must be created beforehand with `dimension = EMBEDDING_DIM` and metric `cosine`.

`docker-compose.yml`
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: agent_gateway
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes:
  pgdata:
```
Plain Postgres is enough. Vectors live in Pinecone, so the `pgvector` extension is not needed.

---

## 6. Data Model

`app/db.py`
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with SessionLocal() as session:
        yield session

async def init_db():
    import app.models.tables  # noqa: F401  (registers tables)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
```
Dev uses `create_all`. Switch to Alembic before sharing the schema.

`app/models/tables.py`
```python
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field

def _now() -> datetime:
    return datetime.now(timezone.utc)

def ts():
    return Field(default_factory=_now, sa_column=Column(DateTime(timezone=True)))

def jb():
    return Field(default_factory=dict, sa_column=Column(JSONB))

def pk():
    return Field(default_factory=uuid4, primary_key=True)

class User(SQLModel, table=True):
    __tablename__ = "users"
    id: UUID = pk()
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = ts()

class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"
    id: UUID = pk()
    user_id: UUID = Field(foreign_key="users.id", index=True)
    key_hash: str = Field(unique=True, index=True)   # sha256 hex
    prefix: str                                       # first 8 chars, for display
    created_at: datetime = ts()

class Provider(SQLModel, table=True):
    __tablename__ = "providers"
    id: UUID = pk()
    user_id: UUID = Field(foreign_key="users.id", index=True)
    provider: str                                     # "openrouter" | "nvidia"
    api_key_enc: str                                  # Fernet ciphertext
    created_at: datetime = ts()

class Agent(SQLModel, table=True):
    __tablename__ = "agents"
    id: UUID = pk()
    user_id: UUID = Field(foreign_key="users.id", index=True)
    name: str
    provider_id: UUID = Field(foreign_key="providers.id")
    config: dict = jb()                               # validated AgentConfig JSON
    created_at: datetime = ts()
    updated_at: datetime = ts()

class Tool(SQLModel, table=True):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: UUID = pk()
    user_id: UUID = Field(foreign_key="users.id", index=True)
    name: str
    description: str
    kind: str                                         # "webhook" | "client"
    endpoint_url: str | None = None
    auth_enc: str | None = None                       # encrypted full header value, e.g. "Bearer xyz"
    input_schema: dict = jb()
    timeout_ms: int = 10000

class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_bases"
    id: UUID = pk()
    user_id: UUID = Field(foreign_key="users.id", index=True)
    name: str
    provider_id: UUID = Field(foreign_key="providers.id")   # must be an embedding-capable provider (nvidia)
    namespace: str                                    # Pinecone namespace = str(id)
    embedding_model: str
    created_at: datetime = ts()

class KbDocument(SQLModel, table=True):
    __tablename__ = "kb_documents"
    id: UUID = pk()
    kb_id: UUID = Field(foreign_key="knowledge_bases.id", index=True)
    filename: str
    status: str = "pending"                           # pending | ingesting | ready | failed
    error: str | None = None
    chunk_count: int = 0
    created_at: datetime = ts()

class AgentSession(SQLModel, table=True):
    __tablename__ = "sessions"
    id: UUID = pk()
    agent_id: UUID = Field(foreign_key="agents.id", index=True)
    facts: dict = jb()                                # {"items": ["fact", ...]}
    created_at: datetime = ts()

class Run(SQLModel, table=True):
    __tablename__ = "runs"
    id: UUID = pk()
    agent_id: UUID = Field(foreign_key="agents.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    session_id: UUID | None = Field(default=None, foreign_key="sessions.id")
    status: str = "running"                           # running | done | failed
    input: str
    variables: dict = jb()
    result: dict = jb()
    state: dict = jb()                                # final RunState snapshot
    stop_reason: str | None = None
    tokens_used: int = 0
    duration_ms: int = 0
    created_at: datetime = ts()

class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"
    id: UUID = pk()
    run_id: UUID = Field(foreign_key="runs.id", index=True)
    local_id: str                                     # "a1", "a2" (id inside the run)
    kind: str
    summary: str
    content: str
```

There is **no chat-history table**. Continuity comes from `AgentSession.facts` plus the run's own state.

---

## 7. Providers & Security Basics

### 7.1 Encryption and keys

`app/security.py`
```python
import asyncio, hashlib, secrets
import bcrypt
from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException
from sqlmodel import select
from app.config import settings
from app.db import get_db
from app.models.tables import ApiKey, User

_fernet = Fernet(settings.encryption_key.encode())

def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()

def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def new_api_key() -> tuple[str, str, str]:
    """Returns (raw, hash, prefix). The raw key is shown to the user exactly once."""
    raw = "gw_" + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:8]

async def hash_password(pw: str) -> str:
    return (await asyncio.to_thread(bcrypt.hashpw, pw.encode(), bcrypt.gensalt())).decode()

async def check_password(pw: str, hashed: str) -> bool:
    return await asyncio.to_thread(bcrypt.checkpw, pw.encode(), hashed.encode())

async def current_user(authorization: str = Header(...), db=Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    key = (await db.exec(select(ApiKey).where(ApiKey.key_hash == hash_key(token)))).first()
    if not key:
        raise HTTPException(401, "Invalid API key")
    user = await db.get(User, key.user_id)
    if not user:
        raise HTTPException(401, "Invalid API key")
    return user
```

### 7.2 LLM provider interface

`app/providers/base.py`
```python
from abc import ABC
import httpx
from langchain_openai import ChatOpenAI

class LLMProvider(ABC):
    provider_id: str
    display_name: str
    base_url: str
    validation_model: str      # a cheap model id used for the key test call

    def get_chat_model(self, model: str, api_key: str, temperature: float = 0.3,
                       max_tokens: int = 2048) -> ChatOpenAI:
        # max_retries=0 on purpose: WE own retries (engine/retry.py).
        # stream_usage=True so token usage is returned while streaming.
        return ChatOpenAI(model=model, api_key=api_key, base_url=self.base_url,
                          temperature=temperature, max_tokens=max_tokens,
                          stream_usage=True, max_retries=0, timeout=60)

    async def validate_api_key(self, api_key: str) -> bool:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": self.validation_model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "hi"}]},
            )
        return r.status_code == 200
```

`app/providers/openrouter.py`
```python
from .base import LLMProvider

class OpenRouterProvider(LLMProvider):
    provider_id = "openrouter"
    display_name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    validation_model = "meta-llama/llama-3.1-8b-instruct"   # verify it exists
```

`app/providers/nvidia.py`
```python
from .base import LLMProvider

class NvidiaProvider(LLMProvider):
    provider_id = "nvidia"
    display_name = "NVIDIA NIM"
    base_url = "https://integrate.api.nvidia.com/v1"
    validation_model = "meta/llama-3.1-8b-instruct"          # verify it exists
```

`app/providers/registry.py`
```python
from .base import LLMProvider
from .openrouter import OpenRouterProvider
from .nvidia import NvidiaProvider

_PROVIDERS: dict[str, LLMProvider] = {p.provider_id: p for p in (OpenRouterProvider(), NvidiaProvider())}

def get_provider(provider_id: str) -> LLMProvider:
    if provider_id not in _PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_id}")
    return _PROVIDERS[provider_id]

def list_providers() -> list[str]:
    return list(_PROVIDERS)
```

Adding a provider means adding one class and one entry in `_PROVIDERS`, assuming it is OpenAI-compatible.

---

## 8. Agent Config

`app/models/agent_config.py`
```python
from typing import Literal
from uuid import UUID
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
    provider_id: UUID
    models: RoleModels
    system_prompt: str                   # Jinja2 template (the agent's persona/rules)
    variables: list[str] = []            # declared runtime variable names
    tools: list[str] = []                # names of the user's registered tools
    use_platform_tools: bool = True      # future hook (section 10.6)
    knowledge_base_ids: list[UUID] = []
    temperature: float = 0.3
    limits: Limits = Limits()
    output_format: Literal["text"] = "text"   # "json" is a later feature

    @field_validator("models", mode="before")
    @classmethod
    def _shorthand(cls, v):
        # allow "models": "model-id"  ->  same model for all three roles
        return {"planner": v, "executor": v, "reviewer": v} if isinstance(v, str) else v
```

Example (create agent body):
```json
{
  "name": "pdf-assistant",
  "provider_id": "<provider uuid>",
  "models": {
    "planner": "meta-llama/llama-3.1-70b-instruct",
    "executor": "meta-llama/llama-3.1-70b-instruct",
    "reviewer": "meta-llama/llama-3.1-8b-instruct"
  },
  "system_prompt": "You are a PDF assistant inside {{ app_name }}. Cite page numbers.",
  "variables": ["app_name", "document_title"],
  "tools": ["highlight_pdf", "crm_lookup"],
  "knowledge_base_ids": [],
  "limits": { "max_iterations": 5, "timeout_s": 120 }
}
```

---

## 9. Agent Engine

### 9.1 Types, state, and LLM output schemas

`app/engine/types.py`
```python
from typing import Literal, TypedDict
from pydantic import BaseModel

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
    facts: list[str] = []
    problems: list[str] = []

class StepVerdict(BaseModel):
    step_id: str
    verdict: Literal["ok", "retry_step", "replan", "abort"]
    reason: str = ""

class ReviewResult(BaseModel):
    verdicts: list[StepVerdict]
```

### 9.2 Run context

`app/engine/context.py`
```python
from __future__ import annotations
import asyncio, time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import UUID

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from app.models.agent_config import AgentConfig
    from app.tools.registry import ToolRegistry
    from app.tools.base import ToolResult
    from app.knowledge.retriever import KnowledgeRetriever

class EventSink:
    """Engine -> API channel. The SSE endpoint drains `queue`. `None` marks end of stream."""
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: str, data: dict) -> None:
        await self.queue.put((event, data))

    async def close(self) -> None:
        await self.queue.put(None)

class Usage:
    def __init__(self):
        self.by_role: dict = defaultdict(lambda: {"in": 0, "out": 0})

    def add_message(self, role: str, msg) -> None:
        um = getattr(msg, "usage_metadata", None) or {}
        if um:
            self.by_role[role]["in"] += um.get("input_tokens", 0)
            self.by_role[role]["out"] += um.get("output_tokens", 0)
        else:  # provider returned no usage: rough estimate (~4 chars/token)
            text = msg.content if isinstance(getattr(msg, "content", ""), str) else ""
            self.by_role[role]["out"] += len(text) // 4

    @property
    def total(self) -> int:
        return sum(v["in"] + v["out"] for v in self.by_role.values())

    def snapshot(self) -> dict:
        return {"total_tokens": self.total, "by_role": {k: dict(v) for k, v in self.by_role.items()}}

class ArtifactStore:
    """In-memory artifact store for one run. Persisted to Postgres when the run ends."""
    def __init__(self):
        self._items: dict[str, dict] = {}
        self._n = 0

    def put(self, kind: str, content: str, summary: str) -> str:
        self._n += 1
        aid = f"a{self._n}"
        self._items[aid] = {"kind": kind, "content": content, "summary": summary}
        return aid

    def get(self, aid: str) -> dict | None:
        return self._items.get(aid)

    def meta(self) -> dict[str, dict]:
        return {k: {"kind": v["kind"], "summary": v["summary"], "size": len(v["content"])}
                for k, v in self._items.items()}

    def all(self) -> dict[str, dict]:
        return self._items

class ClientToolBroker:
    """Lets POST /runs/{id}/tool-result resolve a tool call that is awaiting the client."""
    def __init__(self):
        self._futures: dict[str, asyncio.Future] = {}

    def expect(self, call_id: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._futures[call_id] = fut
        return fut

    def resolve(self, call_id: str, payload) -> bool:
        fut = self._futures.pop(call_id, None)
        if fut and not fut.done():
            fut.set_result(payload)
            return True
        return False

    def discard(self, call_id: str) -> None:
        self._futures.pop(call_id, None)

    def cancel_all(self) -> None:
        for fut in self._futures.values():
            if not fut.done():
                fut.set_result({"error": "run cancelled"})
        self._futures.clear()

@dataclass
class RunContext:
    run_id: str
    user_id: UUID
    config: "AgentConfig"
    persona: str                                  # rendered system prompt
    llm_factory: Callable[[str], "BaseChatModel"] # role -> chat model
    tools: "ToolRegistry"
    retriever: "KnowledgeRetriever | None" = None
    kb_namespaces: list[str] = field(default_factory=list)
    sink: EventSink = field(default_factory=EventSink)
    usage: Usage = field(default_factory=Usage)
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    broker: ClientToolBroker = field(default_factory=ClientToolBroker)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    metrics: Counter = field(default_factory=Counter)
    tool_cache: dict = field(default_factory=dict)      # call signature -> ToolResult (ok only)
    sig_failures: Counter = field(default_factory=Counter)
    started: float = field(default_factory=time.monotonic)
    _llms: dict = field(default_factory=dict)

    def llm(self, role: str):
        if role not in self._llms:
            self._llms[role] = self.llm_factory(role)
        return self._llms[role]

    def check_limits(self) -> str | None:
        """Return a stop reason if the run must end now, else None."""
        lim = self.config.limits
        if self.cancel.is_set():
            return "cancelled"
        if time.monotonic() - self.started > lim.timeout_s:
            return "timeout"
        if self.usage.total > lim.max_tokens_per_run:
            return "budget"
        return None

# Active runs, keyed by run_id (MVP: in-memory, single process).
RUNS: dict[str, RunContext] = {}

# Strong references to background tasks so they are not garbage-collected mid-run.
BACKGROUND: set = set()

def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    BACKGROUND.add(task)
    task.add_done_callback(BACKGROUND.discard)
    return task
```

### 9.3 Retry primitives

`app/engine/retry.py`
```python
import asyncio, hashlib, json, random
import httpx, openai

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}

def classify(exc: Exception) -> str:
    """'transient' = safe to retry the same call; anything else = do not blindly retry."""
    if isinstance(exc, (openai.RateLimitError, openai.APIConnectionError,
                        openai.APITimeoutError, openai.InternalServerError)):
        return "transient"
    if isinstance(exc, httpx.HTTPStatusError):
        return "transient" if exc.response.status_code in TRANSIENT_STATUS else "fatal"
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, asyncio.TimeoutError)):
        return "transient"
    return "fatal"

async def retry_async(fn, *, attempts: int = 3, base: float = 0.5, cap: float = 8.0, on_retry=None):
    """Await fn() with exponential backoff + jitter. Only transient errors are retried."""
    for n in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            if classify(exc) != "transient" or n == attempts - 1:
                raise
            delay = min(cap, base * (2 ** n)) * (0.5 + random.random() / 2)
            headers = getattr(getattr(exc, "response", None), "headers", None)
            retry_after = headers.get("retry-after") if headers else None
            if retry_after and str(retry_after).isdigit():
                delay = max(delay, min(float(retry_after), 30.0))
            if on_retry:
                await on_retry(n + 1, exc)
            await asyncio.sleep(delay)

def call_signature(tool: str, args: dict) -> str:
    """Stable id for 'same tool + same args'. Used for caching, idempotency, and loop detection."""
    raw = f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

def plan_hash(steps: list[dict]) -> str:
    raw = "|".join(sorted(s["goal"].strip().lower() for s in steps))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]
```

### 9.4 LLM helpers (the only place that calls models)

`app/engine/llm_utils.py`
```python
import json, logging
from pydantic import BaseModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from app.engine.retry import retry_async

log = logging.getLogger(__name__)

class BadOutputError(Exception):
    pass

def extract_json(text: str) -> dict:
    """Pull the first {...} object out of free text (handles markdown code fences and chatter)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise BadOutputError("no JSON object found")
    return json.loads(text[start:end + 1])

async def structured_call(ctx, role: str, schema: type[BaseModel], system: str, user: str,
                          attempts: int = 3) -> BaseModel:
    """
    Ask the model for output matching `schema`.
    1) native structured output (tool calling) -> on parse error, retry with the error appended
    2) fallback: plain JSON prompt + extract_json + validate
    Transient HTTP errors are retried inside via retry_async.
    """
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    try:
        runnable = ctx.llm(role).with_structured_output(schema, include_raw=True)
        for _ in range(attempts):
            out = await retry_async(lambda: runnable.ainvoke(messages))
            ctx.usage.add_message(role, out.get("raw"))
            if out.get("parsed") is not None:
                return out["parsed"]
            err = str(out.get("parsing_error") or "empty output")
            ctx.metrics["bad_output_retries"] += 1
            messages = messages + [HumanMessage(content=f"Your last reply did not match the schema ({err}). Reply again with valid output only.")]
    except Exception as exc:  # model without tool support, or provider rejected the schema
        log.warning("structured output failed (%s); falling back to plain JSON", exc)
    return await _plain_json(ctx, role, schema, system, user, attempts)

async def _plain_json(ctx, role, schema, system, user, attempts):
    schema_txt = json.dumps(schema.model_json_schema())
    sys2 = f"{system}\n\nReply with ONLY a JSON object matching this JSON Schema, no other text:\n{schema_txt}"
    messages = [SystemMessage(content=sys2), HumanMessage(content=user)]
    last = ""
    for _ in range(attempts):
        msg = await retry_async(lambda: ctx.llm(role).ainvoke(messages))
        ctx.usage.add_message(role, msg)
        try:
            return schema.model_validate(extract_json(msg.content))
        except Exception as exc:
            last = str(exc)[:200]
            ctx.metrics["bad_output_retries"] += 1
            messages = messages + [AIMessage(content=msg.content),
                                   HumanMessage(content=f"Invalid JSON for the schema: {last}. Reply with the corrected JSON only.")]
    raise BadOutputError(f"could not get valid {schema.__name__}: {last}")

async def llm_turn(ctx, role: str, messages: list, tools: list | None = None,
                   stream_answer: bool = False) -> AIMessage:
    """
    One model turn (optionally with tools bound). Streams chunks; if stream_answer is True the text
    is emitted live as `answer.delta`. If the turn turns out to contain tool calls, or a retry
    happens after text was emitted, an `answer.reset` event tells the client to discard it.
    """
    llm = ctx.llm(role)
    if tools:
        llm = llm.bind_tools(tools)
    emitted = False

    async def call():
        nonlocal emitted
        full = None
        async for chunk in llm.astream(messages):
            full = chunk if full is None else full + chunk
            if stream_answer and isinstance(chunk.content, str) and chunk.content:
                emitted = True
                await ctx.sink.emit("answer.delta", {"text": chunk.content})
        if full is None:
            raise BadOutputError("empty model response")
        return full

    async def on_retry(n, exc):
        nonlocal emitted
        ctx.metrics["llm_transient_retries"] += 1
        if emitted:
            await ctx.sink.emit("answer.reset", {})
            emitted = False

    msg = await retry_async(call, on_retry=on_retry)
    ctx.usage.add_message(role, msg)
    if stream_answer and emitted and getattr(msg, "tool_calls", None):
        await ctx.sink.emit("answer.reset", {})
    return msg
```

### 9.5 Prompts

`app/engine/prompts.py`
```python
ROUTER_SYSTEM = """Decide how to handle a user request.
Answer "direct" if it can be done in one pass with at most one or two tool calls.
Answer "plan" if it needs several dependent steps, multiple tools, or research followed by synthesis."""

PLANNER_SYSTEM = """You are a planner. Break the user's goal into at most {max_steps} steps.
Rules:
- Each step must be doable by a worker that only sees the step goal, its inputs, and the tools you assign.
- Use ids s1, s2, ... On a REPLAN use NEW ids that continue the numbering; never reuse an existing id.
- depends_on: step ids that must finish first. Steps with no dependencies run in parallel.
- inputs: ids of earlier steps whose OUTPUT this step needs (also list them in depends_on).
- tools: pick only from the available tools. Leave empty if the step needs no tools.
- kb_queries: 1-3 short search queries, ONLY if a knowledge base is available and useful.
- success_criteria: one concrete, checkable sentence.
- Do NOT add a final "write the answer" step. The system composes the final answer.
- Prefer fewer steps. A single step is fine for a simple goal.
On a REPLAN: keep completed work, do not repeat it, and avoid approaches listed under DEAD ENDS."""

EXECUTOR_SYSTEM = """You execute ONE step of a larger task. Do only this step.
Use the provided tools when needed. Large tool results are stored as artifacts: call read_artifact(id) to read more.
If a tool call fails, read the error and fix the arguments, or try another approach.
When finished, reply with ONLY a JSON object:
{"status": "done" | "failed", "summary": "<=2 sentences", "output": "the full result later steps need", "facts": ["short durable findings"], "problems": ["what blocked you, if anything"]}"""

DIRECT_SYSTEM = """Answer the user's request. Use tools if they help. If a tool fails, adjust and retry or explain the limitation. Be concise and accurate."""

REVIEWER_SYSTEM = """You review completed steps against their success criteria.
For each step return a verdict:
- ok: the output satisfies the success criteria.
- retry_step: fixable by running the same step again (say precisely what to do differently in `reason`).
- replan: the step is impossible as written or the plan is wrong.
- abort: the overall task cannot be completed.
Be strict about missing or fabricated content, lenient about style."""

FINALIZER_SYSTEM = """Write the final answer to the user from the step results and facts provided.
Be concise and direct. Do not mention internal steps, ids, or tools.
If the run stopped early, say clearly what is incomplete."""
```
`ctx.persona` (the user's rendered system prompt) is prepended to the executor, direct, and finalizer prompts only.

### 9.6 Context views (what each role sees)

`app/engine/views.py`
```python
import json

# character budgets per role (~4 chars per token)
BUDGET = {"planner": 6000, "executor": 8000, "reviewer": 5000, "finalizer": 10000}

def clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(n - 20, 0)] + f"... [+{len(s) - n + 20} chars]"

def assemble(sections: list[tuple[str, str]], budget: int) -> str:
    """Add sections in priority order until the budget is used. Later sections get dropped first."""
    out, used = [], 0
    for title, body in sections:
        if not body:
            continue
        room = budget - used
        if room < 200:
            break
        block = f"## {title}\n{clip(body, room)}\n"
        out.append(block)
        used += len(block)
    return "\n".join(out)

def _facts(state) -> str:
    return "\n".join(f"- {f}" for f in state.get("facts", []))

def _progress(state) -> str:
    return "\n".join(f"[{s['status']}] {s['id']}: {s['goal']} -> {s.get('result_summary') or ''}"
                     for s in state.get("steps", []))

def _inputs(state, ctx, step) -> str:
    by_id = {s["id"]: s for s in state.get("steps", [])}
    blocks = []
    for sid in step.get("inputs", []):
        s = by_id.get(sid)
        if not s or s["status"] != "done":
            continue
        art = ctx.artifacts.get(s.get("artifact_id") or "")
        out = clip(art["content"], 1500) if art else ""
        blocks.append(f"[{sid}] {s.get('result_summary')}\n{out}\n(full output: artifact {s.get('artifact_id')})")
    return "\n\n".join(blocks)

def build_view(role: str, state, ctx, step=None, wave=None, kb_text: str | None = None) -> str:
    variables = json.dumps(state.get("variables", {}), default=str) if state.get("variables") else ""

    if role == "planner":
        tools = "\n".join(ctx.tools.describe(exclude={"read_artifact"}))
        sections = [
            ("GOAL", state["goal"]),
            ("VARIABLES", variables),
            ("AVAILABLE TOOLS", tools or "(none)"),
            ("KNOWLEDGE BASE", "available (use kb_queries / kb_search)" if ctx.kb_namespaces else "not available"),
            ("PROGRESS SO FAR", _progress(state)),
            ("REPLAN REASON", state.get("replan_reason", "")),
            ("DEAD ENDS (do not repeat)", "\n".join(f"- {d}" for d in state.get("dead_ends", []))),
            ("FACTS", _facts(state)),
        ]
        return assemble(sections, BUDGET["planner"])

    if role == "executor":
        knowledge = kb_text if kb_text else ("no relevant results found; try kb_search with a different query"
                                             if step.get("kb_queries") else "")
        sections = [
            ("OVERALL GOAL", state["goal"]),
            ("YOUR STEP", f"{step['id']}: {step['goal']}"),
            ("SUCCESS CRITERIA", step.get("success_criteria", "")),
            ("PREVIOUS ATTEMPT PROBLEMS (fix these)", "\n".join(f"- {p}" for p in step.get("problems", []))),
            ("INPUTS FROM EARLIER STEPS", _inputs(state, ctx, step)),
            ("FACTS", _facts(state)),
            ("KNOWLEDGE", knowledge),
            ("VARIABLES", variables),
        ]
        return assemble(sections, BUDGET["executor"])

    if role == "reviewer":
        blocks = []
        for s in wave or []:
            art = ctx.artifacts.get(s.get("artifact_id") or "")
            blocks.append(
                f"[{s['id']}] goal: {s['goal']}\nsuccess criteria: {s.get('success_criteria', '')}\n"
                f"status: {s['status']}\nsummary: {s.get('result_summary')}\n"
                f"output: {clip(art['content'], 1500) if art else ''}\nproblems: {s.get('problems', [])}")
        return assemble([("OVERALL GOAL", state["goal"]), ("STEPS TO REVIEW", "\n\n".join(blocks))],
                        BUDGET["reviewer"])

    if role == "finalizer":
        steps = state.get("steps", [])
        per_step = max(1500, 8000 // max(len(steps), 1))
        blocks = []
        for s in steps:
            if s["status"] != "done":
                blocks.append(f"[NOT DONE] {s['goal']}")
                continue
            art = ctx.artifacts.get(s.get("artifact_id") or "")
            blocks.append(f"{s['goal']}\n{clip(art['content'], per_step) if art else s.get('result_summary')}")
        stop = state.get("stop_reason") or "done"
        sections = [
            ("USER REQUEST", state["goal"]),
            ("RUN STOPPED EARLY", f"reason: {stop}" if stop != "done" else ""),
            ("STEP RESULTS", "\n\n".join(blocks)),
            ("FACTS", _facts(state)),
        ]
        return assemble(sections, BUDGET["finalizer"])

    raise ValueError(f"unknown role {role}")
```

### 9.7 State operations (pure helpers)

`app/engine/state_ops.py`
```python
from app.engine.types import Plan, Step, StepResult
from app.engine.views import clip

class PlanError(Exception):
    pass

def validate_plan(plan: Plan, allowed_tools: set[str], used_ids: set[str],
                  done_ids: set[str], max_steps: int) -> list[Step]:
    """
    Turn an LLM Plan into validated Steps.
    - new step ids must be unique and must not reuse any id in `used_ids`
    - depends_on / inputs may only reference new ids or `done_ids`
    - unknown tool names are dropped; inputs are merged into depends_on
    - dependency cycles are rejected
    """
    raw = plan.steps[:max_steps]
    if not raw:
        raise PlanError("plan has no steps")
    new_ids = [s.id for s in raw]
    if len(set(new_ids)) != len(new_ids):
        raise PlanError("duplicate step ids in plan")
    clash = set(new_ids) & used_ids
    if clash:
        raise PlanError(f"step ids already used: {sorted(clash)}; use new ids")
    known = set(new_ids) | done_ids
    steps: list[Step] = []
    for s in raw:
        inputs = [i for i in dict.fromkeys(s.inputs) if i in known and i != s.id]
        deps = [d for d in dict.fromkeys(s.depends_on + inputs) if d in known and d != s.id]
        steps.append(Step(
            id=s.id, goal=s.goal, tools=[t for t in s.tools if t in allowed_tools],
            inputs=inputs, kb_queries=s.kb_queries[:3], depends_on=deps,
            success_criteria=s.success_criteria, status="pending", attempts=0,
            result_summary=None, artifact_id=None, problems=[]))
    _check_acyclic(steps, set(new_ids))
    return steps

def _check_acyclic(steps: list[Step], new_ids: set[str]) -> None:
    deps = {s["id"]: {d for d in s["depends_on"] if d in new_ids} for s in steps}
    while deps:
        free = [k for k, v in deps.items() if not v]
        if not free:
            raise PlanError("plan has a dependency cycle")
        for k in free:
            deps.pop(k)
        for v in deps.values():
            v.difference_update(free)

def ready_steps(steps: list[Step]) -> list[Step]:
    done = {s["id"] for s in steps if s["status"] == "done"}
    return [s for s in steps if s["status"] == "pending" and all(d in done for d in s["depends_on"])]

def merge_facts(old: list[str], new: list[str], cap: int = 20) -> list[str]:
    out = list(old)
    seen = {f.strip().lower() for f in out}
    for f in new:
        f = clip(f.strip(), 200)
        if f and f.lower() not in seen:
            out.append(f)
            seen.add(f.lower())
    return out[-cap:]

def apply_result(local: dict, ctx, step_id: str, res: StepResult) -> None:
    """
    Record a StepResult. MUTATES `local`, a node-local dict with keys steps/facts/artifacts.
    Never pass the incoming LangGraph state here; copy first.
    """
    step = next(s for s in local["steps"] if s["id"] == step_id)
    aid = ctx.artifacts.put("step_output", res.output or res.summary, res.summary)
    step["status"] = res.status
    step["result_summary"] = clip(res.summary, 400)
    step["artifact_id"] = aid
    step["problems"] = res.problems
    local["facts"] = merge_facts(local["facts"], res.facts)
    local["artifacts"] = ctx.artifacts.meta()

def fallback_answer(state) -> str:
    """Used when the run stops early and an LLM call is not appropriate (budget/timeout/cancel)."""
    reason = state.get("stop_reason") or "done"
    done = [s for s in state.get("steps", []) if s["status"] == "done"]
    head = "The request could not be fully completed" + (f" ({reason})." if reason != "done" else ".")
    if not done:
        return head
    return head + "\nCompleted so far:\n" + "\n".join(f"- {s['goal']}: {s.get('result_summary')}" for s in done)
```

### 9.8 Tool loop (runs ONE step, or the whole request in direct mode)

`app/engine/tool_loop.py`
```python
import asyncio, logging
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from app.engine import llm_utils as llm
from app.engine.prompts import EXECUTOR_SYSTEM
from app.engine.types import StepResult, StopRun
from app.engine.views import build_view, clip
from app.tools.call import call_tool

log = logging.getLogger(__name__)

async def run_tool_loop(ctx, system: str, user: str, tool_names: list[str],
                        stream_answer: bool = False) -> tuple[str, int]:
    """
    Mini ReAct loop: model turn -> run tool calls (in parallel) -> feed results -> repeat.
    Returns (final_text, number_of_failed_tool_calls). Raises StopRun on cancel/timeout/budget.
    The final allowed turn is made WITHOUT tools so the model must answer.
    """
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    schemas = ctx.tools.schemas(tool_names)
    max_turns = ctx.config.limits.max_tool_turns
    errors = 0
    for turn in range(max_turns + 1):
        if reason := ctx.check_limits():
            raise StopRun(reason)
        last = turn == max_turns
        ai = await llm.llm_turn(ctx, "executor", messages,
                                tools=None if (last or not schemas) else schemas,
                                stream_answer=stream_answer)
        if not ai.tool_calls:
            return (ai.content if isinstance(ai.content, str) else str(ai.content)), errors
        messages.append(ai)
        results = await asyncio.gather(*[call_tool(ctx, tc["name"], tc["args"]) for tc in ai.tool_calls])
        for tc, res in zip(ai.tool_calls, results):
            errors += 0 if res.ok else 1
            messages.append(ToolMessage(content=res.model_dump_json(exclude_none=True),
                                        tool_call_id=tc["id"], name=tc["name"]))
    return "", errors

async def prefetch_kb(ctx, queries: list[str]) -> str:
    """Run the planner-declared kb_queries before a step and format the hits."""
    if not queries or not ctx.retriever or not ctx.kb_namespaces:
        return ""
    try:
        results = await asyncio.gather(*[ctx.retriever.search(ctx.kb_namespaces, q) for q in queries[:3]])
    except Exception as exc:
        log.warning("kb prefetch failed: %s", exc)
        ctx.metrics["kb_errors"] += 1
        return ""
    seen, lines = set(), []
    for chunks in results:
        for c in chunks:
            if c.id not in seen:
                seen.add(c.id)
                lines.append(f"[{c.source}] {c.text}")
    return clip("\n\n".join(lines), 3000)

async def parse_step_result(ctx, text: str) -> StepResult:
    try:
        return StepResult.model_validate(llm.extract_json(text))
    except Exception:
        ctx.metrics["bad_output_retries"] += 1
    if not text.strip():
        return StepResult(status="failed", summary="Empty response", problems=["model returned no content"])
    try:  # one cheap repair attempt
        return await llm.structured_call(ctx, "reviewer", StepResult,
                                         "Convert the text into the required JSON. Do not add information.",
                                         text, attempts=1)
    except Exception:
        return StepResult(status="done", summary=clip(text.strip(), 200), output=text)

async def run_step(ctx, state, step) -> StepResult:
    kb_text = await prefetch_kb(ctx, step.get("kb_queries", []))
    view = build_view("executor", state, ctx, step=step, kb_text=kb_text)
    names = set(step.get("tools", [])) | {"read_artifact"}
    if ctx.kb_namespaces:
        names.add("kb_search")
    text, _errors = await run_tool_loop(ctx, f"{ctx.persona}\n\n{EXECUTOR_SYSTEM}", view, sorted(names))
    return await parse_step_result(ctx, text)
```

### 9.9 Graph nodes

`app/engine/nodes.py`
```python
import asyncio, logging
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from app.engine import llm_utils as llm
from app.engine.prompts import (DIRECT_SYSTEM, FINALIZER_SYSTEM, PLANNER_SYSTEM,
                                REVIEWER_SYSTEM, ROUTER_SYSTEM)
from app.engine.retry import plan_hash
from app.engine.state_ops import (PlanError, apply_result, fallback_answer, ready_steps,
                                  validate_plan)
from app.engine.tool_loop import run_step, run_tool_loop
from app.engine.types import Plan, ReviewResult, RouteDecision, RunState, StepResult, StepVerdict, StopRun
from app.engine.views import build_view, clip

log = logging.getLogger(__name__)
MAX_STEP_RETRIES = 2          # retries per step (attempt 0 + 2 retries)

def get_ctx(config: RunnableConfig):
    return config["configurable"]["ctx"]

# ---------------- router ----------------
async def router_node(state: RunState, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    await ctx.sink.emit("run.started", {"run_id": ctx.run_id})
    visible = [n for n in ctx.tools.names() if n != "read_artifact"]
    forced = ctx.config.limits.force_mode
    if forced:
        mode = forced
    elif not visible and not ctx.kb_namespaces:
        mode = "direct"                       # nothing to plan around
    else:
        d = await llm.structured_call(ctx, "reviewer", RouteDecision, ROUTER_SYSTEM,
                                      f"Request:\n{state['input']}\n\nAvailable tools: {', '.join(visible) or 'none'}")
        mode = d.mode
    await ctx.sink.emit("route", {"mode": mode})
    return {"mode": mode, "goal": state["input"], "steps": [], "iteration": 0, "last_wave": [],
            "plan_hashes": [], "dead_ends": [], "pending_replan": False, "artifacts": {}, "stop_reason": None}

# ---------------- direct (simple requests) ----------------
async def direct_node(state: RunState, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    user = state["input"]
    if state.get("facts"):
        user += "\n\nKnown facts:\n" + "\n".join(f"- {f}" for f in state["facts"])
    try:
        text, _ = await run_tool_loop(ctx, f"{ctx.persona}\n\n{DIRECT_SYSTEM}", user,
                                      ctx.tools.names(), stream_answer=True)
    except StopRun as e:
        return {"stop_reason": e.reason, "answer": ""}
    return {"answer": text, "stop_reason": "done"}

# ---------------- planner ----------------
async def planner_node(state: RunState, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    replan = bool(state.get("pending_replan"))
    old = state.get("steps", [])
    dead = list(state.get("dead_ends", []))
    if replan:
        for s in old:
            if s["status"] != "done":
                dead.append(clip(f"{s['goal']} -> {'; '.join(s.get('problems', []))}", 250))
    dead = dead[-10:]
    work = {**state, "dead_ends": dead}
    allowed = {n for n in ctx.tools.names() if n != "read_artifact"}
    used = {s["id"] for s in old}
    done_ids = {s["id"] for s in old if s["status"] == "done"}
    lim = ctx.config.limits

    new_steps, err = None, ""
    for _ in range(2):
        try:
            plan = await llm.structured_call(ctx, "planner", Plan,
                                             PLANNER_SYSTEM.format(max_steps=lim.max_steps),
                                             build_view("planner", work, ctx) + err)
            new_steps = validate_plan(plan, allowed, used, done_ids, lim.max_steps)
            break
        except PlanError as e:
            err = f"\n\n## YOUR PREVIOUS PLAN WAS INVALID\n{e}. Fix it."
            ctx.metrics["bad_output_retries"] += 1
        except llm.BadOutputError:
            break
    if new_steps is None:
        return {"stop_reason": "unrecoverable"}

    h = plan_hash(new_steps)
    if replan and h in state.get("plan_hashes", []):
        return {"stop_reason": "unrecoverable"}      # identical plan again = no progress
    kept = [s for s in old if s["status"] == "done"] if replan else []
    await ctx.sink.emit("plan.created", {"replan": replan,
                                         "steps": [{"id": s["id"], "goal": s["goal"]} for s in new_steps]})
    return {"steps": kept + new_steps, "plan_hashes": state.get("plan_hashes", []) + [h],
            "pending_replan": False, "dead_ends": dead}

# ---------------- executor (one wave of ready steps) ----------------
async def _safe_run_step(ctx, state, step):
    try:
        return step["id"], await run_step(ctx, state, step), None
    except StopRun as e:
        return step["id"], None, e.reason
    except Exception as e:
        log.exception("step %s crashed", step["id"])
        res = StepResult(status="failed", summary="Step crashed",
                         problems=[f"{type(e).__name__}: {str(e)[:200]}"])
        return step["id"], res, None

async def executor_node(state: RunState, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    if reason := ctx.check_limits():
        return {"stop_reason": reason}
    ready = ready_steps(state["steps"])[: ctx.config.limits.max_parallel_steps]
    local = {"steps": [dict(s) for s in state["steps"]], "facts": list(state.get("facts", [])),
             "artifacts": dict(state.get("artifacts", {}))}
    ready_ids = {r["id"] for r in ready}
    for s in local["steps"]:
        if s["id"] in ready_ids:
            s["status"] = "running"
    for r in ready:
        await ctx.sink.emit("step.started", {"step_id": r["id"], "goal": r["goal"], "attempt": r["attempts"] + 1})

    # run the wave in parallel against the SAME (unchanged) incoming state
    outcomes = await asyncio.gather(*[_safe_run_step(ctx, state, r) for r in ready])

    stop = None
    for step_id, res, stop_reason in outcomes:
        if stop_reason:
            stop = stop or stop_reason
            next(s for s in local["steps"] if s["id"] == step_id)["status"] = "pending"
            continue
        apply_result(local, ctx, step_id, res)
        step = next(s for s in local["steps"] if s["id"] == step_id)
        await ctx.sink.emit("step.done", {"step_id": step_id, "status": step["status"],
                                          "summary": step["result_summary"]})
    out = {**local, "last_wave": [r["id"] for r in ready]}
    if stop:
        out["stop_reason"] = stop
    return out

# ---------------- reviewer ----------------
def deterministic_check(step) -> StepVerdict | None:
    """Cheap checks first. Returns a verdict, or None if an LLM review is needed."""
    sid = step["id"]
    if step["status"] == "failed":
        reason = "; ".join(step.get("problems", [])) or "step failed"
        return StepVerdict(step_id=sid, verdict="retry_step", reason=reason)
    if step["status"] == "done" and not (step.get("result_summary") or "").strip():
        return StepVerdict(step_id=sid, verdict="retry_step", reason="empty result")
    if step["status"] == "done" and not step.get("success_criteria"):
        return StepVerdict(step_id=sid, verdict="ok")
    return None

async def reviewer_node(state: RunState, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    steps = [dict(s) for s in state["steps"]]
    by_id = {s["id"]: s for s in steps}
    wave = [by_id[i] for i in state.get("last_wave", []) if i in by_id]

    verdicts: dict[str, StepVerdict] = {}
    need_llm = []
    for s in wave:
        v = deterministic_check(s)
        if v:
            verdicts[s["id"]] = v
        elif ctx.config.limits.llm_review:
            need_llm.append(s)
        else:
            verdicts[s["id"]] = StepVerdict(step_id=s["id"], verdict="ok")
    if need_llm:
        review = await llm.structured_call(ctx, "reviewer", ReviewResult, REVIEWER_SYSTEM,
                                           build_view("reviewer", state, ctx, wave=need_llm))
        for v in review.verdicts:
            if v.step_id in by_id:
                verdicts[v.step_id] = v
        for s in need_llm:
            verdicts.setdefault(s["id"], StepVerdict(step_id=s["id"], verdict="ok"))

    replan, abort, reasons = False, False, []
    for sid, v in verdicts.items():
        s = by_id[sid]
        if v.verdict == "ok":
            continue
        s["problems"] = list(s.get("problems", [])) + [v.reason]
        if v.verdict == "retry_step" and s["attempts"] < MAX_STEP_RETRIES:
            s["status"], s["attempts"] = "pending", s["attempts"] + 1
            ctx.metrics["step_retries"] += 1
        elif v.verdict == "abort":
            abort = True
            reasons.append(v.reason)
        else:                                   # replan requested, or retries exhausted
            s["status"] = "failed"
            replan = True
            reasons.append(v.reason)

    all_done = all(s["status"] == "done" for s in steps)
    if not all_done and not replan and not abort and not ready_steps(steps):
        replan = True                           # remaining steps are blocked by failed dependencies
        reasons.append("remaining steps are blocked by failed steps")

    iteration = state.get("iteration", 0) + 1
    stop = None
    if abort:
        stop = "unrecoverable"
    elif not all_done:
        stop = ctx.check_limits() or ("max_iterations" if iteration >= ctx.config.limits.max_iterations else None)
    if replan and not stop:
        ctx.metrics["replans"] += 1

    await ctx.sink.emit("review.done", {"verdicts": {k: v.verdict for k, v in verdicts.items()}})
    out = {"steps": steps, "iteration": iteration, "pending_replan": bool(replan and not stop),
           "replan_reason": "; ".join(reasons)[:300]}
    if stop:
        out["stop_reason"] = stop
    return out

# ---------------- finalize ----------------
async def finalize_node(state: RunState, config: RunnableConfig) -> dict:
    ctx = get_ctx(config)
    stop = state.get("stop_reason") or "done"
    if stop in ("budget", "timeout", "cancelled"):
        return {"answer": fallback_answer(state), "stop_reason": stop}   # no LLM call
    msg = await llm.llm_turn(ctx, "executor",
                             [SystemMessage(content=f"{ctx.persona}\n\n{FINALIZER_SYSTEM}"),
                              HumanMessage(content=build_view("finalizer", state, ctx))],
                             stream_answer=True)
    return {"answer": msg.content if isinstance(msg.content, str) else str(msg.content), "stop_reason": stop}
```

### 9.10 Graph wiring

`app/engine/graph.py`
```python
from langgraph.graph import END, START, StateGraph
from app.engine.nodes import (direct_node, executor_node, finalize_node, planner_node,
                              reviewer_node, router_node)
from app.engine.types import RunState

def after_planner(state) -> str:
    return "finalize" if state.get("stop_reason") else "executor"

def after_executor(state) -> str:
    return "finalize" if state.get("stop_reason") else "reviewer"

def after_reviewer(state) -> str:
    if state.get("stop_reason"):
        return "finalize"
    if state.get("pending_replan"):
        return "planner"
    if all(s["status"] == "done" for s in state["steps"]):
        return "finalize"
    return "executor"     # reviewer guarantees at least one ready step here

def build_graph():
    g = StateGraph(RunState)
    g.add_node("router", router_node)
    g.add_node("direct", direct_node)
    g.add_node("planner", planner_node)
    g.add_node("executor", executor_node)
    g.add_node("reviewer", reviewer_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", lambda s: s["mode"], {"direct": "direct", "plan": "planner"})
    g.add_edge("direct", END)
    g.add_conditional_edges("planner", after_planner, {"executor": "executor", "finalize": "finalize"})
    g.add_conditional_edges("executor", after_executor, {"reviewer": "reviewer", "finalize": "finalize"})
    g.add_conditional_edges("reviewer", after_reviewer,
                            {"executor": "executor", "planner": "planner", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()

GRAPH = build_graph()
```

### 9.11 Flow rules (summary)

| Situation | What happens |
|---|---|
| Simple request, or no tools and no KB | Router sends it to `direct`: one tool loop, streamed answer |
| Complex request | Planner → Executor wave → Reviewer → (executor again \| planner \| finalize) |
| Transient LLM/tool error (429, 5xx, timeout) | Retried in place with backoff + jitter. No replan |
| Invalid tool arguments | Validation error returned to the model as the tool result; it fixes the call |
| Model output does not match schema | Re-ask with the error, then plain-JSON fallback |
| Step failed or empty | Reviewer verdict `retry_step`, up to `MAX_STEP_RETRIES`, with the problem shown to the executor |
| Retries exhausted, or reviewer says `replan` | Failed steps become DEAD ENDS. Planner replans, keeping done steps |
| Replan identical to a previous plan | Stop `unrecoverable` |
| Cancel, timeout, token budget, `max_iterations` | Stop with that reason. Return a partial answer |

**Stop reasons:** `done | max_iterations | budget | timeout | cancelled | unrecoverable`

---

## 10. Tools

### 10.1 Tool interface and result

`app/tools/base.py`
```python
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.engine.context import RunContext

class ToolResult(BaseModel):
    ok: bool
    output: Any = None
    error: str | None = None
    artifact_id: str | None = None      # set when a large output was moved to an artifact

class Tool(ABC):
    name: str
    description: str                    # LLM-facing: say WHEN to use it
    input_schema: dict                  # JSON Schema (type: object)
    kind: str = "builtin"               # builtin | webhook | client
    retryable: bool = True              # False when a human/UI is involved
    cacheable: bool = True              # same args -> reuse the earlier OK result
    timeout_ms: int = 10000

    @abstractmethod
    async def run(self, args: dict, ctx: "RunContext") -> ToolResult: ...

    def openai_schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.input_schema}}
```

`app/tools/registry.py`
```python
from app.tools.base import Tool

class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, names: list[str]) -> list[dict]:
        return [self._tools[n].openai_schema() for n in names if n in self._tools]

    def describe(self, exclude: set[str] = frozenset()) -> list[str]:
        return [f"{t.name}: {t.description}" for t in self._tools.values() if t.name not in exclude]

RESERVED_TOOL_NAMES = {"read_artifact", "kb_search"}

def build_tool_registry(user_tool_rows: list, config, has_kb: bool) -> ToolRegistry:
    """User tools named in config.tools + built-ins (+ platform tools later)."""
    from app.tools.builtin import KbSearchTool, ReadArtifactTool, platform_tools
    from app.tools.client import ClientTool
    from app.tools.webhook import WebhookTool
    from app.security import decrypt

    tools: list[Tool] = [ReadArtifactTool()]
    for row in user_tool_rows:
        if row.name not in config.tools:
            continue
        if row.kind == "webhook":
            auth = decrypt(row.auth_enc) if row.auth_enc else None
            tools.append(WebhookTool(row, auth))
        elif row.kind == "client":
            tools.append(ClientTool(row))
    if has_kb:
        tools.append(KbSearchTool())
    if config.use_platform_tools:                       # user tools win on name collisions
        have = {t.name for t in tools}
        tools += [t for t in platform_tools() if t.name not in have]
    return ToolRegistry(tools)
```

### 10.2 The one function that executes any tool

`app/tools/call.py`
```python
import asyncio, json
import httpx, jsonschema
from app.engine.retry import call_signature, retry_async
from app.tools.base import Tool, ToolResult

async def call_tool(ctx, name: str, args: dict) -> ToolResult:
    """
    Validate -> dedupe -> run (with retry for transient errors) -> cap output -> cache.
    Never raises: failures come back as ToolResult(ok=False, error=...) so the MODEL can react.
    """
    tool = ctx.tools.get(name)
    if tool is None:
        return ToolResult(ok=False, error=f"Unknown tool '{name}'. Available: {', '.join(ctx.tools.names())}")
    try:
        jsonschema.validate(args, tool.input_schema)
    except jsonschema.ValidationError as e:
        ctx.metrics["bad_args"] += 1
        return ToolResult(ok=False, error=f"Invalid arguments: {e.message}")

    sig = call_signature(name, args)
    if tool.cacheable and sig in ctx.tool_cache:
        return ctx.tool_cache[sig]                     # also protects side effects from replays
    if ctx.sig_failures[sig] >= 2:                     # same failing call repeated: no-progress guard
        return ToolResult(ok=False, error="This exact call already failed twice. Use different arguments or another tool.")

    await ctx.sink.emit("tool.started", {"tool": name, "kind": tool.kind})

    async def on_retry(n, exc):
        ctx.metrics["tool_transient_retries"] += 1

    try:
        if tool.retryable:
            async def attempt():
                return await asyncio.wait_for(tool.run(args, ctx), timeout=tool.timeout_ms / 1000 + 2)
            result = await retry_async(attempt, attempts=3, on_retry=on_retry)
        else:
            result = await tool.run(args, ctx)
    except httpx.HTTPStatusError as e:
        result = ToolResult(ok=False, error=f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        result = ToolResult(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")

    result = _cap_output(ctx, tool, result)
    if result.ok:
        if tool.cacheable:
            ctx.tool_cache[sig] = result
    else:
        ctx.sig_failures[sig] += 1
        ctx.metrics["tool_errors"] += 1
    await ctx.sink.emit("tool.finished", {"tool": name, "ok": result.ok})
    return result

def _cap_output(ctx, tool: Tool, result: ToolResult) -> ToolResult:
    """Big outputs go to an artifact; the model gets a preview + id and can call read_artifact."""
    if not result.ok or tool.name == "read_artifact":
        return result
    text = result.output if isinstance(result.output, str) else json.dumps(result.output, default=str)
    limit = ctx.config.limits.max_tool_output_chars
    if len(text) <= limit:
        return result
    aid = ctx.artifacts.put("tool_output", text, f"{tool.name} output")
    return ToolResult(ok=True, artifact_id=aid,
                      output={"preview": text[: limit // 2], "truncated": True, "total_chars": len(text)})
```

### 10.3 Built-in tools (always available)

`app/tools/builtin.py`
```python
from app.engine.views import clip
from app.tools.base import Tool, ToolResult

class ReadArtifactTool(Tool):
    name = "read_artifact"
    description = "Read the full content of a stored artifact by id (ids look like a1, a2). Use offset/limit to page through large content."
    input_schema = {"type": "object",
                    "properties": {"id": {"type": "string"},
                                   "offset": {"type": "integer", "minimum": 0},
                                   "limit": {"type": "integer", "minimum": 1, "maximum": 4000}},
                    "required": ["id"]}

    async def run(self, args, ctx) -> ToolResult:
        item = ctx.artifacts.get(args["id"])
        if item is None:
            return ToolResult(ok=False, error=f"No artifact '{args['id']}'. Known: {list(ctx.artifacts.all())}")
        off, lim = args.get("offset", 0), args.get("limit", 4000)
        text = item["content"]
        return ToolResult(ok=True, output={"id": args["id"], "content": text[off: off + lim],
                                           "total_chars": len(text), "has_more": off + lim < len(text)})

class KbSearchTool(Tool):
    name = "kb_search"
    description = "Search the attached knowledge base for domain information. Use short, specific queries; rephrase if nothing relevant comes back."
    input_schema = {"type": "object",
                    "properties": {"query": {"type": "string"},
                                   "top_k": {"type": "integer", "minimum": 1, "maximum": 10}},
                    "required": ["query"]}

    async def run(self, args, ctx) -> ToolResult:
        if not ctx.retriever or not ctx.kb_namespaces:
            return ToolResult(ok=False, error="No knowledge base attached")
        chunks = await ctx.retriever.search(ctx.kb_namespaces, args["query"], final_k=args.get("top_k", 5))
        if not chunks:
            return ToolResult(ok=True, output="No relevant results. Try different wording.")
        return ToolResult(ok=True, output=[{"source": c.source, "text": clip(c.text, 800),
                                            "score": round(c.score, 3)} for c in chunks])

def platform_tools() -> list[Tool]:
    """Future hook: tools WE ship (web_search, summarize, http_request, ...). Empty for now."""
    return []
```

### 10.4 User tool: webhook

`app/tools/webhook.py`
```python
import httpx
from app.engine.retry import call_signature
from app.tools.base import Tool, ToolResult

class WebhookTool(Tool):
    kind = "webhook"

    def __init__(self, row, auth_header: str | None):
        self.name, self.description = row.name, row.description
        self.input_schema, self.timeout_ms = row.input_schema, row.timeout_ms
        self.url, self.auth_header = row.endpoint_url, auth_header

    async def run(self, args, ctx) -> ToolResult:
        headers = {"Content-Type": "application/json",
                   "Idempotency-Key": f"{ctx.run_id}:{call_signature(self.name, args)}"}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
            r = await client.post(self.url, json=args, headers=headers)
            r.raise_for_status()                    # HTTPStatusError is classified by retry.classify
        try:
            return ToolResult(ok=True, output=r.json())
        except ValueError:
            return ToolResult(ok=True, output=r.text)
```
The `Idempotency-Key` header lets the user's server ignore duplicate deliveries when the gateway retries.

### 10.5 User tool: client-executed (pause and resume)

`app/tools/client.py`
```python
import asyncio
from uuid import uuid4
from app.tools.base import Tool, ToolResult

class ClientTool(Tool):
    """The CALLING APP executes this (e.g. highlight text in its own UI), then posts the result back."""
    kind = "client"
    retryable = False          # a human/UI is involved: never auto-retry
    cacheable = False          # results may depend on live UI state

    def __init__(self, row):
        self.name, self.description = row.name, row.description
        self.input_schema, self.timeout_ms = row.input_schema, row.timeout_ms

    async def run(self, args, ctx) -> ToolResult:
        call_id = uuid4().hex[:12]
        fut = ctx.broker.expect(call_id)
        await ctx.sink.emit("tool.call", {"call_id": call_id, "tool": self.name,
                                          "arguments": args, "target": "client"})
        try:
            payload = await asyncio.wait_for(fut, timeout=self.timeout_ms / 1000)
        except asyncio.TimeoutError:
            ctx.broker.discard(call_id)
            return ToolResult(ok=False, error="The client did not respond in time.")
        if isinstance(payload, dict) and payload.get("error"):
            return ToolResult(ok=False, error=str(payload["error"])[:200])
        return ToolResult(ok=True, output=payload)
```
Flow: engine emits `tool.call` → client acts → `POST /v1/runs/{id}/tool-result {call_id, result}` → the broker resolves the future → the engine continues. Set `timeout_ms` on client tools generously (for example 30000).

### 10.6 Later: platform tools (design only)

Goal: agents work even when the user registers no tools. `platform_tools()` returns tools we ship (`web_search`, `summarize`, `http_request`, `code_execute`), each a `Tool` subclass. `build_tool_registry` already merges them when `use_platform_tools` is true and the name is not taken by a user tool. Nothing else in the engine changes.

---

## 11. Knowledge (Pinecone)

**Rules:** one Pinecone index (created beforehand, `dimension = EMBEDDING_DIM`, cosine). One **namespace per knowledge base** (`namespace = str(kb.id)`). Chunk ids are `{doc_id}#{n}` so a document can be deleted by id prefix. All KBs attached to one agent must use the same embedding model/provider.

`app/knowledge/embedder.py`
```python
import httpx
from app.engine.retry import retry_async

class NimEmbedder:
    """NVIDIA NIM embeddings (asymmetric model: passages and queries use different input_type)."""
    def __init__(self, api_key: str, model: str, base_url: str = "https://integrate.api.nvidia.com/v1"):
        self.api_key, self.model, self.base_url = api_key, model, base_url

    async def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        async def call():
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{self.base_url}/embeddings",
                                 headers={"Authorization": f"Bearer {self.api_key}"},
                                 json={"model": self.model, "input": texts, "input_type": input_type,
                                       "encoding_format": "float", "truncate": "END"})
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
        return await retry_async(call)

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "passage")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "query"))[0]
```

`app/knowledge/retriever.py`
```python
import asyncio, functools, logging, time
from abc import ABC, abstractmethod
from pydantic import BaseModel
from pinecone import Pinecone
from app.config import settings

log = logging.getLogger(__name__)

class ChunkIn(BaseModel):
    id: str
    text: str
    embedding: list[float]
    source: str
    doc_id: str
    metadata: dict = {}

class Chunk(BaseModel):
    id: str
    text: str
    score: float
    source: str

class KnowledgeRetriever(ABC):
    embedder: object
    @abstractmethod
    async def upsert(self, namespace: str, chunks: list[ChunkIn]) -> None: ...
    @abstractmethod
    async def search(self, namespaces: list[str], query: str, top_k: int = 20,
                     final_k: int = 5, min_score: float | None = None) -> list[Chunk]: ...
    @abstractmethod
    async def delete_doc(self, namespace: str, doc_id: str) -> None: ...
    @abstractmethod
    async def delete_namespace(self, namespace: str) -> None: ...
    @abstractmethod
    async def wait_visible(self, namespace: str, vec_id: str, vec: list[float], timeout: float = 15.0) -> bool: ...

@functools.cache
def _pc() -> Pinecone:
    return Pinecone(api_key=settings.pinecone_api_key)

@functools.cache
def _index():
    return _pc().Index(host=settings.pinecone_index_host)

class PineconeRetriever(KnowledgeRetriever):
    """The Pinecone SDK calls here are synchronous, so they run in threads (asyncio.to_thread).
    Verify method names against the installed `pinecone` version."""

    def __init__(self, embedder):
        self.embedder = embedder

    async def upsert(self, namespace, chunks):
        vectors = [{"id": c.id, "values": c.embedding,
                    "metadata": {"text": c.text, "source": c.source, "doc_id": c.doc_id, **c.metadata}}
                   for c in chunks]
        for i in range(0, len(vectors), 100):
            await asyncio.to_thread(_index().upsert, vectors=vectors[i:i + 100], namespace=namespace)

    async def search(self, namespaces, query, top_k=20, final_k=5, min_score=None):
        qvec = await self.embedder.embed_query(query)

        async def one(ns):
            res = await asyncio.to_thread(_index().query, vector=qvec, top_k=top_k,
                                          namespace=ns, include_metadata=True)
            return res.matches

        matches = [m for batch in await asyncio.gather(*[one(ns) for ns in namespaces]) for m in batch]
        matches.sort(key=lambda m: m.score, reverse=True)
        matches = matches[:top_k]
        chunks = [Chunk(id=m.id, text=(m.metadata or {}).get("text", ""), score=m.score,
                        source=(m.metadata or {}).get("source", "unknown")) for m in matches]
        chunks = await self._rerank(query, chunks, final_k)
        if min_score is not None:
            chunks = [c for c in chunks if c.score >= min_score]
        return chunks[:final_k]

    async def _rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        if len(chunks) <= 1:
            return chunks
        try:
            res = await asyncio.to_thread(_pc().inference.rerank, model=settings.rerank_model, query=query,
                                          documents=[c.text for c in chunks], top_n=top_n,
                                          return_documents=False)
            return [chunks[r.index].model_copy(update={"score": r.score}) for r in res.data]
        except Exception as exc:                      # reranker unavailable: keep dense order
            log.warning("rerank failed (%s); using vector order", exc)
            return chunks

    async def delete_doc(self, namespace, doc_id):
        def collect_and_delete():
            ids = []
            for page in _index().list(prefix=f"{doc_id}#", namespace=namespace):
                ids.extend(page)
            for i in range(0, len(ids), 500):
                _index().delete(ids=ids[i:i + 500], namespace=namespace)
        await asyncio.to_thread(collect_and_delete)

    async def delete_namespace(self, namespace):
        await asyncio.to_thread(_index().delete, delete_all=True, namespace=namespace)

    async def wait_visible(self, namespace, vec_id, vec, timeout=15.0) -> bool:
        """Upserts are eventually consistent. Poll until the vector is queryable."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            res = await asyncio.to_thread(_index().query, vector=vec, top_k=3, namespace=namespace)
            if any(m.id == vec_id for m in res.matches):
                return True
            await asyncio.sleep(1)
        return False
```

`app/knowledge/ingest.py`
```python
import asyncio, io, logging
from uuid import UUID
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from app.db import SessionLocal
from app.knowledge.retriever import ChunkIn
from app.models.tables import KbDocument

log = logging.getLogger(__name__)

def load_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="ignore")          # .txt / .md

def split_text(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)   # ~500 tokens
    return [c for c in splitter.split_text(text) if c.strip()]

async def ingest_document(doc_id: UUID, namespace: str, filename: str, data: bytes, retriever) -> None:
    """Run as a background task. Updates KbDocument.status: ingesting -> ready | failed."""
    async with SessionLocal() as db:
        doc = await db.get(KbDocument, doc_id)
        doc.status = "ingesting"
        db.add(doc)
        await db.commit()
        try:
            chunks = split_text(await asyncio.to_thread(load_text, filename, data))
            if not chunks:
                raise ValueError("no text could be extracted")
            embeddings: list[list[float]] = []
            for i in range(0, len(chunks), 32):
                embeddings += await retriever.embedder.embed_passages(chunks[i:i + 32])
            items = [ChunkIn(id=f"{doc_id}#{i}", text=c, embedding=e, source=filename, doc_id=str(doc_id))
                     for i, (c, e) in enumerate(zip(chunks, embeddings))]
            await retriever.upsert(namespace, items)
            if not await retriever.wait_visible(namespace, items[0].id, items[0].embedding):
                log.warning("doc %s upserted but not yet queryable after timeout", doc_id)
            doc.status, doc.chunk_count = "ready", len(items)
        except Exception as exc:
            log.exception("ingest failed for %s", doc_id)
            doc.status, doc.error = "failed", str(exc)[:300]
        db.add(doc)
        await db.commit()
```

**Retrieval policy:** the planner can add `kb_queries` to a step (fetched before the step runs, via `prefetch_kb`), and the model can call `kb_search` itself. If a prefetch returns nothing, the executor view says so and suggests rephrasing with `kb_search`. Tune `min_score` on your own queries. Do not guess a threshold.

---

## 12. Run Lifecycle

`app/engine/runner.py`
```python
import asyncio, json, logging, time
from uuid import UUID
from jinja2.sandbox import SandboxedEnvironment
from app.engine.context import RUNS, RunContext
from app.engine.graph import GRAPH
from app.engine.state_ops import fallback_answer
from app.models.agent_config import AgentConfig
from app.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

def render_prompt(template: str, variables: dict) -> str:
    """Tenant-authored templates MUST render in a sandbox."""
    return SandboxedEnvironment().from_string(template).render(**variables)

def build_context(*, run_id: str, user_id: UUID, cfg: AgentConfig, api_key: str, provider,
                  tools: ToolRegistry, retriever, kb_namespaces: list[str], variables: dict) -> RunContext:
    temps = {"planner": 0.2, "reviewer": 0.0}

    def factory(role: str):
        return provider.get_chat_model(getattr(cfg.models, role), api_key,
                                       temperature=temps.get(role, cfg.temperature))
    return RunContext(run_id=run_id, user_id=user_id, config=cfg, persona=render_prompt(cfg.system_prompt, variables),
                      llm_factory=factory, tools=tools, retriever=retriever, kb_namespaces=kb_namespaces)

async def run_graph(ctx: RunContext, user_input: str, variables: dict, facts: list[str]) -> dict:
    """Run the engine and return the final RunState. Tests and scripts call this directly."""
    state = {"run_id": ctx.run_id, "input": user_input, "variables": variables,
             "facts": facts, "artifacts": {}}
    return await GRAPH.ainvoke(state, config={"recursion_limit": 80, "configurable": {"ctx": ctx}})

async def execute_run(ctx: RunContext, run_id: UUID, session_id: UUID | None,
                      user_input: str, variables: dict, facts: list[str]) -> None:
    """Background task started by the API. Always ends by closing the sink."""
    t0 = time.monotonic()
    RUNS[ctx.run_id] = ctx
    status, result, final = "failed", {}, {}
    try:
        final = await asyncio.wait_for(run_graph(ctx, user_input, variables, facts),
                                       timeout=ctx.config.limits.timeout_s + 30)
        stop = final.get("stop_reason") or "done"
        result = {
            "answer": final.get("answer") or fallback_answer(final),
            "stop_reason": stop,
            "steps": [{"id": s["id"], "goal": s["goal"], "status": s["status"],
                       "summary": s.get("result_summary")} for s in final.get("steps", [])],
            "artifacts": ctx.artifacts.meta(),
            "usage": ctx.usage.snapshot(),
            "metrics": dict(ctx.metrics),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }
        status = "done"
        await ctx.sink.emit("run.done", result)
    except asyncio.TimeoutError:
        await ctx.sink.emit("run.error", {"code": "timeout", "message": "run exceeded its time limit"})
    except Exception as exc:
        log.exception("run %s crashed", ctx.run_id)
        await ctx.sink.emit("run.error", {"code": "internal", "message": str(exc)[:200]})
    finally:
        try:
            await _persist(ctx, run_id, session_id, status, result, final, t0)
        except Exception:
            log.exception("persisting run %s failed", ctx.run_id)
        log.info("run_summary %s", json.dumps({"run_id": ctx.run_id, "status": status,
                                               "stop_reason": result.get("stop_reason"),
                                               "tokens": ctx.usage.total, "metrics": dict(ctx.metrics),
                                               "ms": int((time.monotonic() - t0) * 1000)}))
        RUNS.pop(ctx.run_id, None)
        await ctx.sink.close()

async def _persist(ctx, run_id, session_id, status, result, final, t0) -> None:
    from app.db import SessionLocal
    from app.models.tables import AgentSession, Artifact, Run
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        run.status, run.result = status, result
        run.state = json.loads(json.dumps(final, default=str))
        run.stop_reason = result.get("stop_reason")
        run.tokens_used = ctx.usage.total
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        db.add(run)
        for aid, a in ctx.artifacts.all().items():
            db.add(Artifact(run_id=run_id, local_id=aid, kind=a["kind"], summary=a["summary"], content=a["content"]))
        if session_id and final.get("facts") is not None:
            sess = await db.get(AgentSession, session_id)
            sess.facts = {"items": final["facts"]}
            db.add(sess)
        await db.commit()
```

### 12.1 Run result shape

```json
{
  "answer": "...",
  "stop_reason": "done",
  "steps": [{"id": "s1", "goal": "...", "status": "done", "summary": "..."}],
  "artifacts": {"a1": {"kind": "step_output", "summary": "...", "size": 1234}},
  "usage": {"total_tokens": 5120, "by_role": {"planner": {"in": 900, "out": 200}}},
  "metrics": {"step_retries": 1, "replans": 0, "tool_errors": 1, "bad_output_retries": 0,
              "llm_transient_retries": 0, "tool_transient_retries": 0, "bad_args": 0},
  "duration_ms": 8420
}
```

### 12.2 Stream events (SSE)

| Event | Data | Notes |
|---|---|---|
| `run.started` | `{run_id}` | |
| `route` | `{mode}` | `direct` or `plan` |
| `plan.created` | `{replan, steps:[{id,goal}]}` | |
| `step.started` | `{step_id, goal, attempt}` | |
| `tool.started` / `tool.finished` | `{tool, kind}` / `{tool, ok}` | progress for UIs |
| `tool.call` | `{call_id, tool, arguments, target:"client"}` | **client must reply** via `/tool-result` |
| `step.done` | `{step_id, status, summary}` | |
| `review.done` | `{verdicts:{step_id: verdict}}` | |
| `answer.delta` | `{text}` | only the final answer is streamed |
| `answer.reset` | `{}` | discard streamed text so far (a retry or tool call followed) |
| `run.done` | result object (12.1) | last event |
| `run.error` | `{code, message}` | last event |

---

## 13. API

Auth: `Authorization: Bearer gw_...` on every `/v1/*` route. Routers are mounted in `app/main.py`.

### 13.1 Auth

`app/api/auth.py`
```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select
from app.db import get_db
from app.models.tables import ApiKey, User
from app.security import check_password, hash_password, new_api_key

router = APIRouter(prefix="/auth", tags=["auth"])

class Credentials(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)

async def _issue_key(db, user_id) -> str:
    raw, key_hash, prefix = new_api_key()
    db.add(ApiKey(user_id=user_id, key_hash=key_hash, prefix=prefix))
    await db.commit()
    return raw

@router.post("/register")
async def register(body: Credentials, db=Depends(get_db)):
    if (await db.exec(select(User).where(User.email == body.email))).first():
        raise HTTPException(409, "Email already registered")
    user = User(email=body.email, password_hash=await hash_password(body.password))
    db.add(user)
    await db.commit()
    return {"user_id": str(user.id), "api_key": await _issue_key(db, user.id)}   # shown once

@router.post("/login")
async def login(body: Credentials, db=Depends(get_db)):
    user = (await db.exec(select(User).where(User.email == body.email))).first()
    if not user or not await check_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {"api_key": await _issue_key(db, user.id)}
```

### 13.2 Providers

`app/api/providers.py`
```python
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from app.db import get_db
from app.models.tables import Provider
from app.providers.registry import get_provider, list_providers
from app.security import current_user, encrypt

router = APIRouter(prefix="/v1/providers", tags=["providers"])

class ProviderIn(BaseModel):
    provider: str            # one of list_providers()
    api_key: str

@router.post("")
async def add_provider(body: ProviderIn, user=Depends(current_user), db=Depends(get_db)):
    if body.provider not in list_providers():
        raise HTTPException(400, f"provider must be one of {list_providers()}")
    if not await get_provider(body.provider).validate_api_key(body.api_key):
        raise HTTPException(400, "Provider rejected this API key")
    row = Provider(user_id=user.id, provider=body.provider, api_key_enc=encrypt(body.api_key))
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "provider": row.provider}

@router.get("")
async def list_my_providers(user=Depends(current_user), db=Depends(get_db)):
    rows = (await db.exec(select(Provider).where(Provider.user_id == user.id))).all()
    return [{"id": str(r.id), "provider": r.provider} for r in rows]     # never return keys
```

### 13.3 Tools

`app/api/tools.py`
```python
import re
import jsonschema
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from app.db import get_db
from app.models.tables import Tool
from app.security import current_user, encrypt
from app.tools.registry import RESERVED_TOOL_NAMES

router = APIRouter(prefix="/v1/tools", tags=["tools"])

class ToolIn(BaseModel):
    name: str
    description: str
    kind: str                        # "webhook" | "client"
    endpoint_url: str | None = None  # required for webhook
    auth_header: str | None = None   # full header value, e.g. "Bearer xyz" (stored encrypted)
    input_schema: dict
    timeout_ms: int = 10000

@router.post("")
async def register_tool(body: ToolIn, user=Depends(current_user), db=Depends(get_db)):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", body.name) or body.name in RESERVED_TOOL_NAMES:
        raise HTTPException(400, "invalid or reserved tool name")
    if body.kind not in ("webhook", "client"):
        raise HTTPException(400, "kind must be webhook or client")
    if body.kind == "webhook" and not body.endpoint_url:
        raise HTTPException(400, "endpoint_url is required for webhook tools")
    try:
        jsonschema.Draft202012Validator.check_schema(body.input_schema)
    except jsonschema.SchemaError as e:
        raise HTTPException(400, f"input_schema is not valid JSON Schema: {e.message}")
    if body.input_schema.get("type") != "object":
        raise HTTPException(400, "input_schema.type must be 'object'")
    row = Tool(user_id=user.id, name=body.name, description=body.description, kind=body.kind,
               endpoint_url=body.endpoint_url, input_schema=body.input_schema, timeout_ms=body.timeout_ms,
               auth_enc=encrypt(body.auth_header) if body.auth_header else None)
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "name": row.name}

@router.get("")
async def list_tools(user=Depends(current_user), db=Depends(get_db)):
    rows = (await db.exec(select(Tool).where(Tool.user_id == user.id))).all()
    return [{"id": str(r.id), "name": r.name, "kind": r.kind, "description": r.description} for r in rows]
```

### 13.4 Agents

`app/api/agents.py`
```python
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from app.db import get_db
from app.models.agent_config import AgentConfig
from app.models.tables import Agent, KnowledgeBase, Provider, Tool
from app.security import current_user

router = APIRouter(prefix="/v1/agents", tags=["agents"])

async def _validate(cfg: AgentConfig, user, db) -> None:
    prov = await db.get(Provider, cfg.provider_id)
    if not prov or prov.user_id != user.id:
        raise HTTPException(400, "unknown provider_id")
    mine = set((await db.exec(select(Tool.name).where(Tool.user_id == user.id))).all())
    if missing := set(cfg.tools) - mine:
        raise HTTPException(400, f"unknown tools: {sorted(missing)}")
    for kb_id in cfg.knowledge_base_ids:
        kb = await db.get(KnowledgeBase, kb_id)
        if not kb or kb.user_id != user.id:
            raise HTTPException(400, f"unknown knowledge base {kb_id}")

async def _owned(agent_id: UUID, user, db) -> Agent:
    agent = await db.get(Agent, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "agent not found")
    return agent

@router.post("")
async def create_agent(cfg: AgentConfig, user=Depends(current_user), db=Depends(get_db)):
    await _validate(cfg, user, db)
    agent = Agent(user_id=user.id, name=cfg.name, provider_id=cfg.provider_id, config=cfg.model_dump(mode="json"))
    db.add(agent)
    await db.commit()
    return {"id": str(agent.id), "run_url": f"/v1/agents/{agent.id}/runs"}

@router.get("")
async def list_agents(user=Depends(current_user), db=Depends(get_db)):
    rows = (await db.exec(select(Agent).where(Agent.user_id == user.id))).all()
    return [{"id": str(a.id), "name": a.name} for a in rows]

@router.get("/{agent_id}")
async def get_agent(agent_id: UUID, user=Depends(current_user), db=Depends(get_db)):
    agent = await _owned(agent_id, user, db)
    return {"id": str(agent.id), "config": agent.config}

@router.put("/{agent_id}")
async def update_agent(agent_id: UUID, cfg: AgentConfig, user=Depends(current_user), db=Depends(get_db)):
    agent = await _owned(agent_id, user, db)
    await _validate(cfg, user, db)
    agent.name, agent.provider_id = cfg.name, cfg.provider_id
    agent.config = cfg.model_dump(mode="json")
    agent.updated_at = datetime.now(timezone.utc)
    db.add(agent)
    await db.commit()
    return {"id": str(agent.id)}

@router.delete("/{agent_id}")
async def delete_agent(agent_id: UUID, user=Depends(current_user), db=Depends(get_db)):
    await db.delete(await _owned(agent_id, user, db))
    await db.commit()
    return {"deleted": True}
```

### 13.5 Knowledge bases

`app/knowledge/factory.py`
```python
from app.config import settings
from app.knowledge.embedder import NimEmbedder
from app.knowledge.retriever import PineconeRetriever
from app.models.tables import KnowledgeBase, Provider
from app.security import decrypt

async def make_retriever(db, kb: KnowledgeBase) -> PineconeRetriever:
    provider = await db.get(Provider, kb.provider_id)
    return PineconeRetriever(NimEmbedder(decrypt(provider.api_key_enc), kb.embedding_model))
```

`app/api/knowledge.py`
```python
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import select
from app.config import settings
from app.db import get_db
from app.engine.context import spawn
from app.knowledge.factory import make_retriever
from app.knowledge.ingest import ingest_document
from app.models.tables import KbDocument, KnowledgeBase, Provider
from app.security import current_user

router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge"])

class KbIn(BaseModel):
    name: str
    provider_id: UUID            # must be an NVIDIA provider (embeddings)

async def _owned_kb(kb_id: UUID, user, db) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb or kb.user_id != user.id:
        raise HTTPException(404, "knowledge base not found")
    return kb

@router.post("")
async def create_kb(body: KbIn, user=Depends(current_user), db=Depends(get_db)):
    prov = await db.get(Provider, body.provider_id)
    if not prov or prov.user_id != user.id or prov.provider != "nvidia":
        raise HTTPException(400, "provider_id must be one of your NVIDIA providers")
    kb_id = uuid4()
    kb = KnowledgeBase(id=kb_id, user_id=user.id, name=body.name, provider_id=prov.id,
                       namespace=str(kb_id), embedding_model=settings.embedding_model)
    db.add(kb)
    await db.commit()
    return {"id": str(kb.id)}

@router.post("/{kb_id}/documents")
async def upload_document(kb_id: UUID, file: UploadFile, user=Depends(current_user), db=Depends(get_db)):
    kb = await _owned_kb(kb_id, user, db)
    data = await file.read()
    doc = KbDocument(kb_id=kb.id, filename=file.filename or "upload.txt")
    db.add(doc)
    await db.commit()
    retriever = await make_retriever(db, kb)
    spawn(ingest_document(doc.id, kb.namespace, doc.filename, data, retriever))
    return {"id": str(doc.id), "status": "pending"}

@router.get("/{kb_id}/documents")
async def list_documents(kb_id: UUID, user=Depends(current_user), db=Depends(get_db)):
    await _owned_kb(kb_id, user, db)
    rows = (await db.exec(select(KbDocument).where(KbDocument.kb_id == kb_id))).all()
    return [{"id": str(d.id), "filename": d.filename, "status": d.status,
             "chunks": d.chunk_count, "error": d.error} for d in rows]
```
Document delete calls `retriever.delete_doc(kb.namespace, str(doc.id))` and then deletes the row. Add it after ingest works.

### 13.6 Runs (start, stream, poll, tool-result, cancel)

`app/api/runs.py`
```python
import asyncio, json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from jinja2 import TemplateError
from pydantic import BaseModel, Field
from sqlmodel import select
from app.db import get_db
from app.engine.context import RUNS, spawn
from app.engine.runner import build_context, execute_run
from app.knowledge.factory import make_retriever
from app.models.agent_config import AgentConfig
from app.models.tables import Agent, AgentSession, KnowledgeBase, Provider, Run, Tool
from app.providers.registry import get_provider
from app.security import current_user, decrypt
from app.tools.registry import build_tool_registry

router = APIRouter(prefix="/v1", tags=["runs"])

class RunRequest(BaseModel):
    input: str = Field(min_length=1)
    variables: dict = {}
    session_id: UUID | None = None      # reuse to carry facts across runs
    stream: bool = True                 # False -> 202 + poll GET /v1/runs/{id}

class ToolResultIn(BaseModel):
    call_id: str
    result: dict | list | str | int | float | bool | None = None
    error: str | None = None

async def _sse(sink):
    while True:
        try:
            item = await asyncio.wait_for(sink.queue.get(), timeout=15)
        except asyncio.TimeoutError:
            yield ": ping\n\n"                              # keep proxies from closing the stream
            continue
        if item is None:
            return
        event, data = item
        yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

@router.post("/agents/{agent_id}/run")
async def start_run(agent_id: UUID, body: RunRequest, user=Depends(current_user), db=Depends(get_db)):
    agent = await db.get(Agent, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "agent not found")
    cfg = AgentConfig.model_validate(agent.config)
    prov = await db.get(Provider, agent.provider_id)
    rows = (await db.exec(select(Tool).where(Tool.user_id == user.id))).all()
    kbs = [kb for kb in [await db.get(KnowledgeBase, i) for i in cfg.knowledge_base_ids]
           if kb and kb.user_id == user.id]
    retriever = await make_retriever(db, kbs[0]) if kbs else None   # all KBs share one embedder

    if body.session_id:
        session = await db.get(AgentSession, body.session_id)
        if not session or session.agent_id != agent.id:
            raise HTTPException(404, "session not found")
    else:
        session = AgentSession(agent_id=agent.id)
        db.add(session)
    run = Run(agent_id=agent.id, user_id=user.id, session_id=session.id,
              input=body.input, variables=body.variables)
    db.add(run)
    await db.commit()

    try:
        ctx = build_context(run_id=str(run.id), user_id=user.id, cfg=cfg, api_key=decrypt(prov.api_key_enc),
                            provider=get_provider(prov.provider),
                            tools=build_tool_registry(rows, cfg, has_kb=bool(kbs)),
                            retriever=retriever, kb_namespaces=[k.namespace for k in kbs],
                            variables=body.variables)
    except TemplateError as e:
        raise HTTPException(400, f"system_prompt template error: {e}")
    RUNS[ctx.run_id] = ctx           # register now so tool-result/cancel work from the first event
    spawn(execute_run(ctx, run.id, session.id, body.input, body.variables,
                      list(session.facts.get("items", []))))
    if not body.stream:
        return JSONResponse({"run_id": ctx.run_id, "session_id": str(session.id)}, status_code=202)
    return StreamingResponse(_sse(ctx.sink), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, user=Depends(current_user), db=Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run or run.user_id != user.id:
        raise HTTPException(404, "run not found")
    return {"id": str(run.id), "status": run.status, "stop_reason": run.stop_reason,
            "result": run.result, "tokens_used": run.tokens_used, "duration_ms": run.duration_ms}

def _active(run_id: str, user):
    ctx = RUNS.get(run_id)
    if not ctx or ctx.user_id != user.id:
        raise HTTPException(404, "run not active")
    return ctx

@router.post("/runs/{run_id}/tool-result")
async def tool_result(run_id: str, body: ToolResultIn, user=Depends(current_user)):
    ctx = _active(run_id, user)
    payload = {"error": body.error} if body.error else body.result
    if not ctx.broker.resolve(body.call_id, payload):
        raise HTTPException(409, "no pending tool call with that call_id")
    return {"ok": True}

@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user=Depends(current_user)):
    ctx = _active(run_id, user)
    ctx.cancel.set()
    ctx.broker.cancel_all()
    return {"ok": True}
```
In `start_run`, `session.id` is available before the commit because ids are generated in Python (`default_factory=uuid4`).

### 13.7 App entry

`app/main.py`
```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api import agents, auth, knowledge, providers, runs, tools
from app.config import settings
from app.db import init_db

logging.basicConfig(level=settings.log_level)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Agent Gateway", lifespan=lifespan)
for module in (auth, providers, tools, agents, knowledge, runs):
    app.include_router(module.router)
```

---

## 14. Running It

```bash
cp .env.example .env            # fill ENCRYPTION_KEY, PINECONE_*
docker compose up -d db
uvicorn app.main:app --reload --workers 1     # ONE worker (in-memory run registry)
```

End-to-end smoke test:
```bash
# 1. register -> API key
curl -s localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"supersecret1"}'
export KEY=gw_...

# 2. add provider (validated with a test call)
curl -s localhost:8000/v1/providers -H "authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"provider":"openrouter","api_key":"sk-or-..."}'

# 3. create agent
curl -s localhost:8000/v1/agents -H "authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"name":"demo","provider_id":"<id>","models":"meta-llama/llama-3.1-70b-instruct","system_prompt":"You are a helpful assistant."}'

# 4. run (SSE)
curl -N localhost:8000/v1/agents/<agent_id>/runs -H "authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"input":"Explain what a hash map is in 3 sentences."}'
```

---

## 15. Testing

All engine tests run **without a real LLM**: the fixture replaces `llm_utils.structured_call` and `llm_utils.llm_turn` with scripted answers. This is why rule 2 in section 0 exists.

`pytest.ini`
```ini
[pytest]
asyncio_mode = auto
```

`tests/conftest.py`
```python
import os
from cryptography.fernet import Fernet
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage
from app.engine import llm_utils
from app.engine.context import RunContext
from app.models.agent_config import AgentConfig, Limits
from app.tools.builtin import ReadArtifactTool
from app.tools.registry import ToolRegistry

def ai(text: str = "", tool_calls: list | None = None) -> AIMessage:
    return AIMessage(content=text, tool_calls=tool_calls or [])

def make_ctx(tools=None, **limits) -> RunContext:
    cfg = AgentConfig(name="t", provider_id=uuid4(), models="m",
                      system_prompt="You are a test agent.", limits=Limits(**limits))
    return RunContext(run_id="t1", user_id=uuid4(), config=cfg, persona=cfg.system_prompt,
                      llm_factory=lambda role: None,
                      tools=ToolRegistry([ReadArtifactTool(), *(tools or [])]))

class Script:
    """Queue scripted LLM behavior. structured[Schema] = [instances...]; turns = [AIMessage...]."""
    def __init__(self):
        self.structured: dict[type, list] = {}
        self.turns: list[AIMessage] = []

    async def structured_call(self, ctx, role, schema, system, user, attempts=3):
        return self.structured[schema].pop(0)

    async def llm_turn(self, ctx, role, messages, tools=None, stream_answer=False):
        msg = self.turns.pop(0)
        if stream_answer and msg.content and not msg.tool_calls:
            await ctx.sink.emit("answer.delta", {"text": msg.content})
        return msg

@pytest.fixture
def script(monkeypatch):
    s = Script()
    monkeypatch.setattr(llm_utils, "structured_call", s.structured_call)
    monkeypatch.setattr(llm_utils, "llm_turn", s.llm_turn)
    return s
```

`tests/test_state_ops.py`
```python
import pytest
from app.engine.state_ops import PlanError, validate_plan
from app.engine.types import Plan, PlanStep

def test_cycle_rejected():
    plan = Plan(steps=[PlanStep(id="a", goal="x", depends_on=["b"]),
                       PlanStep(id="b", goal="y", depends_on=["a"])])
    with pytest.raises(PlanError):
        validate_plan(plan, set(), set(), set(), 8)

def test_unknown_tools_dropped_and_inputs_become_deps():
    plan = Plan(steps=[PlanStep(id="s1", goal="a", tools=["nope", "web"]),
                       PlanStep(id="s2", goal="b", inputs=["s1"])])
    steps = validate_plan(plan, {"web"}, set(), set(), 8)
    assert steps[0]["tools"] == ["web"]
    assert steps[1]["depends_on"] == ["s1"]

def test_replan_cannot_reuse_ids():
    plan = Plan(steps=[PlanStep(id="s1", goal="again")])
    with pytest.raises(PlanError):
        validate_plan(plan, set(), {"s1"}, set(), 8)
```

`tests/test_flow.py`
```python
import asyncio
from types import SimpleNamespace
from app.engine.runner import run_graph
from app.engine.types import Plan, PlanStep, ReviewResult, StepVerdict
from app.tools.client import ClientTool
from tests.conftest import ai, make_ctx

STEP = PlanStep(id="s1", goal="Find the answer", success_criteria="non-empty answer")
FAILED = '{"status":"failed","summary":"x","problems":["tool timeout"]}'
DONE = '{"status":"done","summary":"found it","output":"42"}'

async def test_step_retry_then_success(script):
    ctx = make_ctx(force_mode="plan")
    script.structured[Plan] = [Plan(steps=[STEP])]
    script.structured[ReviewResult] = [ReviewResult(verdicts=[StepVerdict(step_id="s1", verdict="ok")])]
    script.turns = [ai(FAILED), ai(DONE), ai("The answer is 42")]
    final = await run_graph(ctx, "What is the answer?", {}, [])
    assert final["stop_reason"] == "done"
    assert final["answer"] == "The answer is 42"
    assert ctx.metrics["step_retries"] == 1

async def test_identical_replan_is_unrecoverable(script):
    ctx = make_ctx(force_mode="plan")
    script.structured[Plan] = [Plan(steps=[STEP]),
                               Plan(steps=[PlanStep(id="s2", goal="Find the answer")])]   # same goal
    script.turns = [ai(FAILED), ai(FAILED), ai(FAILED), ai("Could not finish")]
    final = await run_graph(ctx, "What is the answer?", {}, [])
    assert final["stop_reason"] == "unrecoverable"

async def test_client_tool_pause_and_resume(script):
    row = SimpleNamespace(name="highlight", description="Highlight a page",
                          input_schema={"type": "object", "properties": {"page": {"type": "integer"}},
                                        "required": ["page"]}, timeout_ms=3000)
    ctx = make_ctx(tools=[ClientTool(row)], force_mode="direct")
    script.turns = [ai("", [{"name": "highlight", "args": {"page": 2}, "id": "c1"}]), ai("Highlighted page 2")]
    task = asyncio.create_task(run_graph(ctx, "highlight page 2", {}, []))
    while True:
        item = await asyncio.wait_for(ctx.sink.queue.get(), timeout=5)
        if item and item[0] == "tool.call":
            break
    assert ctx.broker.resolve(item[1]["call_id"], {"ok": True})
    final = await asyncio.wait_for(task, timeout=5)
    assert final["answer"] == "Highlighted page 2"
```
`tests/test_tools.py`
```python
import httpx
from app.tools.base import Tool, ToolResult
from app.tools.call import call_tool
from tests.conftest import make_ctx

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}

class FailTool(Tool):
    name, description, input_schema = "fail", "always fails", SCHEMA
    def __init__(self):
        self.calls = 0
    async def run(self, args, ctx):
        self.calls += 1
        return ToolResult(ok=False, error="boom")

class BigTool(Tool):
    name, description, input_schema = "big", "returns a lot of text", SCHEMA
    async def run(self, args, ctx):
        return ToolResult(ok=True, output="x" * 10_000)

class FlakyTool(Tool):
    name, description, input_schema = "flaky", "times out once, then works", SCHEMA
    def __init__(self):
        self.calls = 0
    async def run(self, args, ctx):
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("slow")
        return ToolResult(ok=True, output="ok")

async def test_unknown_tool_and_bad_args():
    ctx = make_ctx(tools=[FailTool()])
    assert "Unknown tool" in (await call_tool(ctx, "nope", {})).error
    assert "Invalid arguments" in (await call_tool(ctx, "fail", {"x": "not-an-int"})).error

async def test_identical_failing_call_is_blocked_on_third_try():
    tool = FailTool()
    ctx = make_ctx(tools=[tool])
    for _ in range(3):
        res = await call_tool(ctx, "fail", {"x": 1})
    assert tool.calls == 2
    assert "already failed" in res.error

async def test_large_output_becomes_artifact():
    ctx = make_ctx(tools=[BigTool()])
    res = await call_tool(ctx, "big", {"x": 1})
    assert res.artifact_id and res.output["truncated"]
    assert len(ctx.artifacts.get(res.artifact_id)["content"]) == 10_000

async def test_transient_error_is_retried_and_ok_result_is_cached():
    tool = FlakyTool()
    ctx = make_ctx(tools=[tool])
    assert (await call_tool(ctx, "flaky", {"x": 1})).ok
    assert ctx.metrics["tool_transient_retries"] == 1
    await call_tool(ctx, "flaky", {"x": 1})          # same call again: served from cache
    assert tool.calls == 2
```

Add `tests/__init__.py` (empty) so `from tests.conftest import ...` works.

### 15.1 Real-model checks

`scripts/ping_llm.py` verifies that the chosen model supports what the engine needs (plain chat, structured output, tool calling):
```python
import asyncio, os, sys
from pydantic import BaseModel
from app.providers.registry import get_provider

class Pong(BaseModel):
    word: str

async def main(provider_id: str, model: str):
    llm = get_provider(provider_id).get_chat_model(model, os.environ["PROVIDER_API_KEY"])
    msg = await llm.ainvoke("Say pong.")
    print("plain       :", msg.content[:60], msg.usage_metadata)
    out = await llm.with_structured_output(Pong, include_raw=True).ainvoke("Return word=pong")
    print("structured  :", out["parsed"], out.get("parsing_error"))
    tool = {"type": "function", "function": {"name": "get_time", "description": "Get the time",
                                             "parameters": {"type": "object", "properties": {}}}}
    msg = await llm.bind_tools([tool]).ainvoke("What time is it? Use the tool.")
    print("tool calling:", bool(msg.tool_calls))

asyncio.run(main(sys.argv[1], sys.argv[2]))
```
Run: `PROVIDER_API_KEY=... python -m scripts.ping_llm openrouter <model-id>`. If `structured` or `tool calling` fails, pick a different model for that role.

`scripts/run_local.py` runs the engine with a real model and prints events (no API, no DB):
```python
import asyncio, os, sys
from uuid import uuid4
from app.engine.runner import build_context, run_graph
from app.models.agent_config import AgentConfig
from app.providers.registry import get_provider
from app.tools.builtin import ReadArtifactTool
from app.tools.registry import ToolRegistry

async def main(task: str):
    cfg = AgentConfig(name="local", provider_id=uuid4(), models=os.environ["MODEL"],
                      system_prompt="You are a helpful assistant.")
    ctx = build_context(run_id="local", user_id=uuid4(), cfg=cfg, api_key=os.environ["PROVIDER_API_KEY"],
                        provider=get_provider(os.environ.get("PROVIDER", "openrouter")),
                        tools=ToolRegistry([ReadArtifactTool()]), retriever=None,
                        kb_namespaces=[], variables={})

    async def printer():
        while (item := await ctx.sink.queue.get()) is not None:
            print(item[0], item[1])
    p = asyncio.create_task(printer())
    final = await run_graph(ctx, task, {}, [])
    await ctx.sink.close()
    await p
    print("\nANSWER:", final.get("answer"), "\nMETRICS:", dict(ctx.metrics), ctx.usage.snapshot())

asyncio.run(main(" ".join(sys.argv[1:])))
```

### 15.2 Golden set (measuring performance)

`golden/tasks.yaml`: 15 to 20 tasks, including ones that should **fail gracefully**.
```yaml
- id: simple-qa
  input: "What is 17 * 23? Reply with the number only."
  expect: { stop_reason: done, contains: ["391"], max_tokens: 4000 }
- id: multi-step
  input: "List three advantages of caching, then rank them by importance and justify the ranking."
  expect: { stop_reason: done, max_steps: 4, max_tokens: 20000 }
- id: flaky-tool
  input: "Use the flaky_lookup tool with key 'alpha' and report what it returns."
  expect: { stop_reason: done, contains: ["alpha"], max_retries_total: 3 }
- id: impossible
  input: "Call the tool named does_not_exist and report its output."
  expect: { stop_reason_in: [done, unrecoverable], max_duration_s: 120 }
```

`scripts/run_golden.py`: for each task, build a context (as in `run_local.py`; register a `FlakyLookup` demo tool that raises `httpx.ReadTimeout` on its first call), run `run_graph`, check the `expect` fields, and print one row per task:

```
id            pass  stop_reason   steps  step_retries  replans  tool_errors  tokens  ms
simple-qa     yes   done          0      0             0        0            612     1900
```

Re-run after **every** change to prompts, limits, or loop logic, and compare the rows. A change that raises tokens or retries without raising the pass rate is a regression.

---

## 16. Build Order (with acceptance checks)

Do these in order. Do not start a step before the previous "Done when" holds.

| # | Build | Done when |
|---|---|---|
| 0 | Repo, `requirements.txt`, `.env`, `docker-compose.yml`, `pytest.ini` | `docker compose up -d db` works; `pytest -q` runs (0 tests) |
| 1 | `config.py`, `db.py`, `models/tables.py`, `models/agent_config.py` | `init_db()` creates all tables; `AgentConfig` accepts `"models": "x"` shorthand |
| 2 | `security.py`, `providers/*` | `python -m scripts.ping_llm <provider> <model>` prints plain, structured, and tool-calling OK |
| 3 | `engine/types.py`, `views.py`, `state_ops.py`, `retry.py` | `tests/test_state_ops.py` passes |
| 4 | `engine/context.py`, `llm_utils.py`, `tools/*` (base, registry, call, builtin, webhook, client) | `tests/test_tools.py` passes (bad args, unknown tool, repeated failing call blocked, large output becomes an artifact, transient retry, cache) |
| 5 | `prompts.py`, `tool_loop.py`, `nodes.py`, `graph.py`, `runner.py` | `tests/test_flow.py` (3 tests) passes with scripted LLM |
| 6 | Real engine run | `python -m scripts.run_local "<task>"` completes and prints events, answer, metrics |
| 7 | `api/*`, `main.py` | the section 14 curl smoke test streams `run.done`; `GET /v1/runs/{id}` returns the stored result |
| 8 | Client tools end to end | a `client` tool call shows as `tool.call` in SSE; `POST /tool-result` resumes the run; `cancel` ends it with `cancelled` |
| 9 | `knowledge/*`, `api/knowledge.py` | upload a doc, status becomes `ready`, an agent with that KB answers from it, and `kb_search` returns hits |
| 10 | `scripts/run_golden.py` + `golden/tasks.yaml` | table prints; baseline pass rate and token use are recorded |

**MVP is done when:** steps 0 to 10 pass, and a webhook tool registered through `/v1/tools` is called by an agent during a planned run.

---

## 17. Later (not part of the MVP)

- **Platform tools** (section 10.6): `web_search`, `summarize`, `http_request`, `code_execute`.
- **JSON output mode:** `output_format: "json"` plus `output_schema`; the finalizer uses `with_structured_output(schema)`.
- **Durable resume:** LangGraph checkpointer plus persisted client-tool state, so a process restart doesn't lose runs.
- **Scale-out:** Redis for the run registry and event fan-out (SSE across processes), worker queue for heavy tools.
- **Hardening:** SSRF protection for webhook and URL tools, rate limits, quotas, tenant isolation, key rotation, JWT login.
- **Config versioning, multi-agent, more KB loaders (DOCX, URL crawl).**

## 18. Known Limitations (MVP)

- **Single process:** `RUNS`, the client-tool broker, and artifacts are in memory. Run exactly one uvicorn worker. A restart drops active runs.
- **Webhook URLs are unrestricted.** Do not expose to untrusted users until SSRF protection exists.
- **No rate limiting, quotas, or cost accounting** (tokens only; if a provider returns no usage, tokens are estimated).
- **One embedder per agent:** all attached KBs must share the same embedding provider and model. Only NVIDIA embeddings are implemented.
- **Pinecone SDK calls** are wrapped with `asyncio.to_thread`; confirm method names against the installed version.
- **Model quality varies:** weak models may ignore tool schemas or the JSON contract. The retry paths cover most of it, but check each role's model with `ping_llm` first.

## 19. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Planner or reviewer always falls back to plain JSON | Model lacks tool-calling or structured-output support. Choose another model for that role |
| `recursion limit` error from LangGraph | Raise `recursion_limit` in `run_graph`, or lower `max_iterations` / `max_steps` |
| Budget never triggers | Provider returns no `usage_metadata`; the estimate is rough. Check `ctx.usage.snapshot()` |
| KB answers empty right after upload | Document status is not `ready` yet, or Pinecone indexing is lagging. Wait for `ready`, then retry |
| Streamed text disappears mid-answer | Client received `answer.reset` (a retry or tool call followed). Clear the buffer and keep reading |
| Run hangs on a client tool | Client never called `/tool-result`. The tool times out after `timeout_ms` and the model sees an error |
| `ImportError` on `app.config` in tests | `ENCRYPTION_KEY` missing; `tests/conftest.py` sets a default, so import it first |
