from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import Column, DateTime, UniqueConstraint, Index
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
    username: str = Field(unique=True, index=True)
    name: str
    organization: str | None = Field(default=None, nullable=True)
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
    enabled_models: list[str] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )                                                  # models the user picked for this provider
    default_model: str | None = Field(default=None, nullable=True)
    created_at: datetime = ts()

class Agent(SQLModel, table=True):
    __tablename__ = "agents"
    id: UUID = pk()
    user_id: UUID = Field(foreign_key="users.id", index=True)
    name: str
    provider_id: UUID = Field(foreign_key="providers.id", ondelete="CASCADE")
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
    provider_id: UUID = Field(foreign_key="providers.id", ondelete="CASCADE")   # must be an embedding-capable provider
    namespace: str                                    # Pinecone namespace = str(id)
    embedding_model: str
    vector_api_key_enc: str | None = None             # Encrypted user Pinecone API key
    vector_index_host: str | None = None              # User's Pinecone Index Host URL
    rerank_model: str | None = Field(default="bge-reranker-v2-m3", nullable=True)
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
    __table_args__ = (
        # Composite index for fast lookups by agent + end_user
        UniqueConstraint("agent_id", "end_user_id", "external_session_id", name="uq_agent_enduser_session"),
    )
    id: UUID = pk()
    agent_id: UUID = Field(foreign_key="agents.id", index=True, ondelete="CASCADE")
    user_id: UUID = Field(foreign_key="users.id", index=True)  # Company/Organization that owns the agent
    
    # End-user identification (from the subscribing company's system)
    end_user_id: str = Field(default="default", index=True)   # Required with default for backward compatibility
    external_session_id: str | None = Field(                  # Optional: company can provide their own session ID
        default=None, nullable=True, index=True
    )
    
    facts: dict = jb()                                # {"items": ["fact", ...]}
    conversation_history: list[dict] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )                                                  # [{"role": "user", "content": "..."}, ...]
    created_at: datetime = ts()
    updated_at: datetime = ts()

class Run(SQLModel, table=True):
    __tablename__ = "runs"
    id: UUID = pk()
    agent_id: UUID = Field(foreign_key="agents.id", index=True, ondelete="CASCADE")
    user_id: UUID = Field(foreign_key="users.id", index=True)  # Company/Organization
    session_id: UUID | None = Field(default=None, foreign_key="sessions.id", ondelete="SET NULL")
    
    # End-user who made this request (from the subscribing company's system)
    end_user_id: str | None = Field(default=None, index=True)  # Optional but recommended for analytics
    
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
    run_id: UUID = Field(foreign_key="runs.id", index=True, ondelete="CASCADE")
    local_id: str                                     # "a1", "a2" (id inside the run)
    kind: str                                         # step_output | code | document | data | error_log | final_answer
    summary: str
    content: str

class AgentKV(SQLModel, table=True):
    __tablename__ = "agent_kv"
    __table_args__ = (
        UniqueConstraint("agent_id", "end_user_id", "key", name="uq_agent_enduser_key"),
    )
    id: UUID = pk()
    agent_id: UUID = Field(foreign_key="agents.id", index=True, ondelete="CASCADE")
    user_id: UUID = Field(foreign_key="users.id", index=True)  # Company/Organization
    
    # End-user scoped storage (each end-user has their own key-value pairs)
    end_user_id: str | None = Field(default=None, index=True)  # None = shared across all end-users
    
    key: str
    value: str                                        # JSON-serialized value
    created_at: datetime = ts()
    updated_at: datetime = ts()