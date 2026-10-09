"""
Built-in gateway tools — always available regardless of user-defined tools.

Each tool receives the RunContext at construction so it can access in-memory
state (artifacts, retriever) and the database (session memory, KV store).
"""
from __future__ import annotations
import json, logging
from typing import TYPE_CHECKING, Any, Optional
from pydantic import BaseModel, Field
from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.engine.context import RunContext

log = logging.getLogger(__name__)

# Input schemas for each tool
class ReadArtifactInput(BaseModel):
    artifact_id: str = Field(..., description="The ID of the artifact to read (e.g., 'a1', 'a2')")

class KBSearchInput(BaseModel):
    query: str = Field(..., description="The search query to find relevant information")
    top_k: int = Field(default=5, description="Number of top results to return")

class RememberFactInput(BaseModel):
    fact: str = Field(..., description="The fact to remember (max 300 chars)")

class StoreKVInput(BaseModel):
    key: str = Field(..., description="The key to store the value under")
    value: Any = Field(..., description="The value to store (can be any JSON type)")

class GetKVInput(BaseModel):
    key: str = Field(..., description="The key to retrieve")

class GetConversationHistoryInput(BaseModel):
    last_n: int = Field(default=10, description="Number of recent messages to retrieve (default: 10)")
    from_date: Optional[str] = Field(default=None, description="ISO date string to filter messages from (e.g., '2024-01-15' or '2024-01-15T10:00:00')")
    to_date: Optional[str] = Field(default=None, description="ISO date string to filter messages until (e.g., '2024-01-16')")
    role: Optional[str] = Field(default=None, description="Filter by role: 'user' or 'assistant'")


# ---------------------------------------------------------------------------
# read_artifact
# ---------------------------------------------------------------------------
class ReadArtifactTool(BaseTool):
    """Read the full content of a previously stored artifact by its local ID (e.g. 'a1')."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "read_artifact"

    @property
    def description(self) -> str:
        return (
            "Read the full content of a previously stored artifact by its ID (e.g. 'a1', 'a2'). "
            "Use list_artifacts() first if you are unsure which IDs exist."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ReadArtifactInput

    async def _execute(self, artifact_id: str) -> ToolResult:
        item = self._ctx.artifacts.get(artifact_id)
        if item is None:
            return ToolResult(ok=False, error=f"Artifact '{artifact_id}' not found. "
                                              f"Available: {list(self._ctx.artifacts.all().keys())}")
        return ToolResult(ok=True, output={
            "id": artifact_id,
            "kind": item["kind"],
            "summary": item["summary"],
            "content": item["content"],
        })


# ---------------------------------------------------------------------------
# list_artifacts
# ---------------------------------------------------------------------------
class ListArtifactsTool(BaseTool):
    """List all artifacts stored so far in this run (id, kind, summary, size)."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "list_artifacts"

    @property
    def description(self) -> str:
        return (
            "List all artifacts produced so far in this run. "
            "Returns id, kind, summary and byte size for each. "
            "Use read_artifact(id) to fetch the full content."
        )

    async def _execute(self) -> ToolResult:
        meta = self._ctx.artifacts.meta()
        if not meta:
            return ToolResult(ok=True, output="No artifacts yet.")
        rows = [
            f"{aid}: [{v['kind']}] {v['summary']!r}  ({v['size']} chars)"
            for aid, v in meta.items()
        ]
        return ToolResult(ok=True, output="\n".join(rows))


# ---------------------------------------------------------------------------
# kb_search
# ---------------------------------------------------------------------------
class KBSearchTool(BaseTool):
    """Semantic search over the knowledge bases attached to this agent."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "kb_search"

    @property
    def description(self) -> str:
        return (
            "Search the knowledge base(s) attached to this agent for relevant information. "
            "Returns the top matching text chunks with their source and relevance score."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return KBSearchInput

    async def _execute(self, query: str, top_k: int = 5) -> ToolResult:
        if not self._ctx.retriever or not self._ctx.kb_namespaces:
            return ToolResult(ok=False, error="No knowledge base is attached to this agent.")
        try:
            chunks = await self._ctx.retriever.search(
                namespaces=self._ctx.kb_namespaces,
                query=query,
                top_k=20,
                final_k=top_k,
            )
            if not chunks:
                return ToolResult(ok=True, output="No relevant results found for that query.")
            lines = [
                f"[score={c['score']:.3f}] [{c['source']}]\n{c['text']}"
                for c in chunks
            ]
            return ToolResult(ok=True, output="\n\n---\n\n".join(lines))
        except Exception as exc:
            log.error("kb_search failed: %s", exc)
            return ToolResult(ok=False, error=f"Knowledge base search failed: {exc}")


# ---------------------------------------------------------------------------
# remember_fact
# ---------------------------------------------------------------------------
class RememberFactTool(BaseTool):
    """Add a short, durable fact to this run's fact list (persisted to the session)."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "remember_fact"

    @property
    def description(self) -> str:
        return (
            "Store a short, durable finding as a fact for this run. "
            "Facts survive across steps and are included in every prompt. "
            "Use this for things worth remembering: confirmed values, user preferences, key discoveries."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return RememberFactInput

    async def _execute(self, fact: str) -> ToolResult:
        fact = fact.strip()
        if not fact:
            return ToolResult(ok=False, error="Fact cannot be empty.")
        if len(fact) > 300:
            fact = fact[:300]
        # Append to the run state facts via a side-channel on ctx
        # (state_ops.merge_facts deduplicates at node boundaries)
        if not hasattr(self._ctx, "_pending_facts"):
            self._ctx._pending_facts = []
        self._ctx._pending_facts.append(fact)
        return ToolResult(ok=True, output=f"Fact recorded: {fact!r}")


# ---------------------------------------------------------------------------
# get_session_memory
# ---------------------------------------------------------------------------
class GetSessionMemoryTool(BaseTool):
    """Read facts remembered in previous runs of this session."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "get_session_memory"

    @property
    def description(self) -> str:
        return (
            "Read durable facts remembered across previous runs of this session. "
            "Returns a list of short fact strings."
        )

    async def _execute(self) -> ToolResult:
        try:
            from app.db import SessionLocal
            from app.models.tables import AgentSession
            from sqlmodel import select
            session_id = getattr(self._ctx, "session_id", None)
            if not session_id:
                return ToolResult(ok=True, output="No session attached to this run.")
            from uuid import UUID
            async with SessionLocal() as db:
                res = await db.exec(select(AgentSession).where(AgentSession.id == UUID(str(session_id))))
                session = res.first()
            if not session:
                return ToolResult(ok=True, output="Session not found.")
            facts = (session.facts or {}).get("items", [])
            if not facts:
                return ToolResult(ok=True, output="No facts in session memory yet.")
            return ToolResult(ok=True, output="\n".join(f"- {f}" for f in facts))
        except Exception as exc:
            log.error("get_session_memory failed: %s", exc)
            return ToolResult(ok=False, error=f"Could not read session memory: {exc}")


# ---------------------------------------------------------------------------
# store_kv
# ---------------------------------------------------------------------------
class StoreKVTool(BaseTool):
    """Persist a key-value pair for this agent (survives across all runs)."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "store_kv"

    @property
    def description(self) -> str:
        return (
            "Store a value under a key for this agent. "
            "Values persist permanently across all runs. "
            "Value can be any JSON-serializable type (string, number, list, dict)."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return StoreKVInput

    async def _execute(self, key: str, value: object) -> ToolResult:
        key = key.strip()
        if not key:
            return ToolResult(ok=False, error="Key cannot be empty.")
        try:
            serialized = json.dumps(value)
        except (TypeError, ValueError) as exc:
            return ToolResult(ok=False, error=f"Value is not JSON-serializable: {exc}")
        try:
            from app.db import SessionLocal
            from app.models.tables import AgentKV
            from sqlmodel import select
            from datetime import datetime, timezone
            agent_id_real = getattr(self._ctx, "agent_id", None)
            end_user_id = getattr(self._ctx, "end_user_id", None)
            
            if not agent_id_real:
                return ToolResult(ok=False, error="Agent ID not set on context; cannot persist KV.")
            from uuid import UUID
            async with SessionLocal() as db:
                res = await db.exec(
                    select(AgentKV).where(
                        AgentKV.agent_id == UUID(str(agent_id_real)),
                        AgentKV.end_user_id == end_user_id,
                        AgentKV.key == key,
                    )
                )
                existing = res.first()
                if existing:
                    existing.value = serialized
                    existing.updated_at = datetime.now(timezone.utc)
                    db.add(existing)
                else:
                    db.add(AgentKV(
                        agent_id=UUID(str(agent_id_real)),
                        user_id=self._ctx.user_id,
                        end_user_id=end_user_id,
                        key=key,
                        value=serialized,
                    ))
                await db.commit()
            return ToolResult(ok=True, output=f"Stored {key!r} = {serialized}")
        except Exception as exc:
            log.error("store_kv failed: %s", exc)
            return ToolResult(ok=False, error=f"store_kv failed: {exc}")


# ---------------------------------------------------------------------------
# get_kv
# ---------------------------------------------------------------------------
class GetKVTool(BaseTool):
    """Read a value previously stored with store_kv."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "get_kv"

    @property
    def description(self) -> str:
        return (
            "Read a value previously stored with store_kv. "
            "Returns the value or null if the key does not exist."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return GetKVInput

    async def _execute(self, key: str) -> ToolResult:
        key = key.strip()
        try:
            from app.db import SessionLocal
            from app.models.tables import AgentKV
            from sqlmodel import select
            agent_id_real = getattr(self._ctx, "agent_id", None)
            end_user_id = getattr(self._ctx, "end_user_id", None)
            
            if not agent_id_real:
                return ToolResult(ok=False, error="Agent ID not set on context.")
            from uuid import UUID
            async with SessionLocal() as db:
                res = await db.exec(
                    select(AgentKV).where(
                        AgentKV.agent_id == UUID(str(agent_id_real)),
                        AgentKV.end_user_id == end_user_id,
                        AgentKV.key == key,
                    )
                )
                row = res.first()
            if row is None:
                return ToolResult(ok=True, output=None)
            return ToolResult(ok=True, output=json.loads(row.value))
        except Exception as exc:
            log.error("get_kv failed: %s", exc)
            return ToolResult(ok=False, error=f"get_kv failed: {exc}")


# ---------------------------------------------------------------------------
# get_conversation_history
# ---------------------------------------------------------------------------
class GetConversationHistoryTool(BaseTool):
    """Retrieve conversation history from previous runs in this session."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "get_conversation_history"

    @property
    def description(self) -> str:
        return (
            "Retrieve conversation history from previous runs in this session. "
            "Returns the last N messages (user questions and assistant answers). "
            "Supports filtering by date range and role. "
            "Examples: get messages from yesterday, filter only user messages, etc."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return GetConversationHistoryInput

    async def _execute(self, last_n: int = 10, from_date: Optional[str] = None, 
                      to_date: Optional[str] = None, role: Optional[str] = None) -> ToolResult:
        try:
            from app.db import SessionLocal
            from app.models.tables import AgentSession
            from sqlmodel import select
            from uuid import UUID
            from datetime import datetime, timezone
            import dateutil.parser
            
            session_id = getattr(self._ctx, "session_id", None)
            if not session_id:
                return ToolResult(ok=True, output="No session attached to this run.")
            
            async with SessionLocal() as db:
                res = await db.exec(select(AgentSession).where(AgentSession.id == UUID(str(session_id))))
                session = res.first()
            
            if not session:
                return ToolResult(ok=True, output="Session not found.")
            
            history = session.conversation_history or []
            if not history:
                return ToolResult(ok=True, output="No conversation history yet.")
            
            # Apply filters
            filtered = history
            
            # Filter by date range
            if from_date:
                try:
                    from_dt = dateutil.parser.parse(from_date)
                    filtered = [
                        msg for msg in filtered 
                        if msg.get("timestamp") and dateutil.parser.parse(msg["timestamp"]) >= from_dt
                    ]
                except Exception as e:
                    return ToolResult(ok=False, error=f"Invalid from_date format: {e}")
            
            if to_date:
                try:
                    to_dt = dateutil.parser.parse(to_date)
                    filtered = [
                        msg for msg in filtered 
                        if msg.get("timestamp") and dateutil.parser.parse(msg["timestamp"]) <= to_dt
                    ]
                except Exception as e:
                    return ToolResult(ok=False, error=f"Invalid to_date format: {e}")
            
            # Filter by role
            if role:
                filtered = [msg for msg in filtered if msg.get("role") == role]
            
            if not filtered:
                return ToolResult(ok=True, output="No messages match the specified filters.")
            
            # Get last N messages after filtering
            recent = filtered[-last_n:] if last_n > 0 else filtered
            
            if not recent:
                return ToolResult(ok=True, output="No messages in the specified range.")
            
            # Format as readable conversation with timestamps
            lines = []
            for msg in recent:
                role_str = msg.get("role", "unknown")
                content = msg.get("content", "")
                timestamp = msg.get("timestamp", "")
                
                if timestamp:
                    lines.append(f"[{timestamp}] [{role_str.upper()}] {content}")
                else:
                    lines.append(f"[{role_str.upper()}] {content}")
            
            return ToolResult(ok=True, output="\n\n".join(lines))
        except Exception as exc:
            log.error("get_conversation_history failed: %s", exc)
            return ToolResult(ok=False, error=f"Could not retrieve conversation history: {exc}")


# ---------------------------------------------------------------------------
# Factory — build all builtin tools for a run
# ---------------------------------------------------------------------------
def build_builtin_tools(ctx: "RunContext") -> list[BaseTool]:
    """Return all gateway built-in tool instances wired to this run's context."""
    tools: list[BaseTool] = [
        ReadArtifactTool(ctx),
        ListArtifactsTool(ctx),
        RememberFactTool(ctx),
        GetSessionMemoryTool(ctx),
        StoreKVTool(ctx),
        GetKVTool(ctx),
        GetConversationHistoryTool(ctx),
    ]
    if ctx.retriever and ctx.kb_namespaces:
        tools.append(KBSearchTool(ctx))
    return tools