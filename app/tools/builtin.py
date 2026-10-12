"""
Built-in gateway tools — always available regardless of user-defined tools.

Each tool receives the RunContext at construction so it can access in-memory
state (artifacts, retriever) and the database (session memory, KV store).
"""
from __future__ import annotations
import asyncio, json, logging
import hashlib
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
    last_n: int = Field(default=10, ge=1, le=100, description="Maximum messages to return, in chronological order (1-100)")
    from_date: Optional[str] = Field(default=None, description="Inclusive ISO-8601 start date/time. Date-only values mean midnight UTC; timestamps without an offset are interpreted as UTC.")
    to_date: Optional[str] = Field(default=None, description="Inclusive ISO-8601 end timestamp. A date-only value includes that whole UTC date. Timestamps without an offset are interpreted as UTC.")
    role: Optional[str] = Field(default=None, pattern="^(user|assistant)$", description="Optionally return only 'user' or 'assistant' messages")


class SearchConversationMemoryInput(BaseModel):
    query: str = Field(..., min_length=1, description="What detail to find in older messages from this session")
    top_k: int = Field(default=3, ge=1, le=5, description="Maximum relevant messages to return")


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

    async def _execute(self, artifact_id: str, **kwargs) -> ToolResult:
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

    async def _execute(self, **kwargs) -> ToolResult:
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

    async def _execute(self, query: str, top_k: int = 5, **kwargs) -> ToolResult:
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

    async def _execute(self, fact: str, **kwargs) -> ToolResult:
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

    async def _execute(self, **kwargs) -> ToolResult:
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

    async def _execute(self, key: str, value: object, **kwargs) -> ToolResult:
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

    async def _execute(self, key: str, **kwargs) -> ToolResult:
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
            "Retrieve older messages from this exact session. Recent turns are already included in your conversation context, "
            "so do NOT call this tool when the answer is visible there. Call it when the user asks to search, list, or "
            "compare earlier turns, asks about a date range, or when required details are outside the visible context. "
            "Use role='user' for questions the user asked, and explicit ISO-8601 UTC date bounds for time filters "
            "(for a date-only to_date, the entire UTC date is included). Results are chronological and exclude the current run."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return GetConversationHistoryInput

    async def _execute(self, last_n: int = 10, from_date: Optional[str] = None, 
                      to_date: Optional[str] = None, role: Optional[str] = None, **kwargs) -> ToolResult:
        try:
            from app.db import SessionLocal
            from app.models.tables import ConversationMessage
            from sqlmodel import select
            from uuid import UUID
            from datetime import datetime, timezone, timedelta
            import dateutil.parser
            from sqlalchemy import or_
            
            session_id = getattr(self._ctx, "session_id", None)
            if not session_id:
                return ToolResult(ok=True, output="No session attached to this run.")

            if not 1 <= last_n <= 100:
                return ToolResult(ok=False, error="last_n must be between 1 and 100.")
            if role not in (None, "user", "assistant"):
                return ToolResult(ok=False, error="role must be 'user' or 'assistant'.")

            def parse_bound(value: str | None, *, upper: bool = False):
                if value is None:
                    return None, False
                try:
                    parsed = dateutil.parser.isoparse(value)
                except (TypeError, ValueError, OverflowError):
                    raise ValueError(f"Invalid {'to_date' if upper else 'from_date'}; use an ISO-8601 date or timestamp.")
                date_only = len(value.strip()) == 10
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
                if upper and date_only:
                    return parsed + timedelta(days=1), True
                return parsed, False

            from_dt, _ = parse_bound(from_date)
            to_dt, to_date_only = parse_bound(to_date, upper=True)
            if from_dt is not None and to_dt is not None and from_dt >= to_dt:
                return ToolResult(ok=False, error="from_date must be earlier than to_date.")
            cache_args = json.dumps(
                {"last_n": last_n, "from_date": from_date, "to_date": to_date, "role": role},
                sort_keys=True, separators=(",", ":"),
            )
            digest = hashlib.sha256(cache_args.encode("utf-8")).hexdigest()
            session_key = str(UUID(str(session_id)))
            cache_value_key = None
            redis_client = None
            try:
                from app.redis_client import get_redis, conversation_history_version
                redis_client = await asyncio.wait_for(get_redis(), timeout=0.2)
                if redis_client:
                    version = await asyncio.wait_for(
                        conversation_history_version(redis_client, session_key), timeout=0.2
                    )
                    cache_value_key = f"history:result:{session_key}:{version}:{digest}"
                    cached = await asyncio.wait_for(redis_client.get(cache_value_key), timeout=0.2)
                    if cached is not None:
                        return ToolResult(ok=True, output=cached)
                    # build_context already loaded this recent transcript from Postgres;
                    # reuse it for the first history lookup when it fully covers the request.
                    if from_date is None and to_date is None:
                        snapshot_key = f"history:recent:{session_key}:{version}"
                        snapshot = await asyncio.wait_for(redis_client.get(snapshot_key), timeout=0.2)
                        if snapshot is not None:
                            snapshot_rows = json.loads(snapshot)
                            if role:
                                snapshot_rows = [row for row in snapshot_rows if row.get("role") == role]
                            if len(snapshot_rows) >= last_n:
                                output = "\n\n".join(
                                    f"[{row['timestamp']}] [{row['role'].upper()}] {row['content']}"
                                    for row in snapshot_rows[-last_n:]
                                )
                                if len(output) > 16000:
                                    output = output[:16000] + "\n\n[History output truncated at 16000 characters.]"
                                await asyncio.wait_for(
                                    redis_client.set(cache_value_key, output, ex=30), timeout=0.2
                                )
                                return ToolResult(ok=True, output=output)
            except Exception as cache_exc:
                log.debug("Conversation history cache read failed: %s", cache_exc)
                redis_client = None
                cache_value_key = None

            query = select(ConversationMessage).where(
                ConversationMessage.session_id == UUID(session_key)
            )
            current_run_id = getattr(self._ctx, "run_id", None)
            if current_run_id:
                query = query.where(or_(
                    ConversationMessage.run_id.is_(None),
                    ConversationMessage.run_id != UUID(str(current_run_id)),
                ))
            if from_dt is not None:
                query = query.where(ConversationMessage.created_at >= from_dt)
            if to_dt is not None:
                if to_date_only:
                    query = query.where(ConversationMessage.created_at < to_dt)
                else:
                    query = query.where(ConversationMessage.created_at <= to_dt)
            if role:
                query = query.where(ConversationMessage.role == role)
            query = query.order_by(
                ConversationMessage.created_at.desc(), ConversationMessage.id.desc()
            ).limit(last_n)
            async with SessionLocal() as db:
                message_res = await db.exec(query)
                message_rows = list(reversed(message_res.all()))

            if not message_rows:
                output = "No messages match the specified filters."
            else:
                output = "\n\n".join(
                    f"[{msg.created_at.isoformat()}] [{msg.role.upper()}] {msg.content}"
                    for msg in message_rows
                )
                if len(output) > 16000:
                    output = output[:16000] + "\n\n[History output truncated at 16000 characters.]"
            if redis_client and cache_value_key:
                try:
                    await asyncio.wait_for(redis_client.set(cache_value_key, output, ex=30), timeout=0.2)
                except Exception as cache_exc:
                    log.debug("Conversation history cache write failed: %s", cache_exc)
            return ToolResult(ok=True, output=output)
        except Exception as exc:
            log.error("get_conversation_history failed: %s", exc)
            return ToolResult(ok=False, error=f"Could not retrieve conversation history: {exc}")


class SearchConversationMemoryTool(BaseTool):
    """Semantically search older messages, restricted to this exact agent session."""

    def __init__(self, ctx: "RunContext"):
        self._ctx = ctx

    @property
    def name(self) -> str:
        return "search_conversation_memory"

    @property
    def description(self) -> str:
        return (
            "Search older messages in the current conversation by meaning. "
            "Use when the recent conversation and session summary do not contain a needed detail. "
            "Results are isolated to this exact session and include timestamps."
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return SearchConversationMemoryInput

    async def _execute(self, query: str, top_k: int = 3, **kwargs) -> ToolResult:
        namespace = getattr(self._ctx, "conversation_namespace", None)
        session_id = getattr(self._ctx, "session_id", None)
        retriever = getattr(self._ctx, "retriever", None)
        if not namespace or not session_id or not retriever:
            return ToolResult(ok=True, output="Semantic conversation memory is not configured for this session.")
        try:
            chunks = await retriever.search(
                namespaces=[namespace],
                query=query,
                top_k=top_k,
                final_k=top_k,
                metadata_filter={
                    "$and": [
                        {"record_type": {"$eq": "conversation_message"}},
                        {"session_id": {"$eq": str(session_id)}},
                        {"user_id": {"$eq": str(getattr(self._ctx, "user_id", ""))}},
                        {"agent_id": {"$eq": str(getattr(self._ctx, "agent_id", ""))}},
                    ]
                },
            )
            if not chunks:
                return ToolResult(ok=True, output="No relevant older messages found.")
            lines = []
            for chunk in chunks:
                metadata = chunk.get("metadata", {})
                lines.append(
                    f"[{metadata.get('timestamp', 'unknown time')}] "
                    f"[{metadata.get('role', 'unknown').upper()}] {chunk.get('text', '')}"
                )
            return ToolResult(ok=True, output="\n\n".join(lines))
        except Exception as exc:
            log.error("search_conversation_memory failed: %s", exc)
            return ToolResult(ok=False, error=f"Conversation memory search failed: {exc}")


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
    if ctx.retriever and getattr(ctx, "conversation_namespace", None) and getattr(ctx, "session_id", None):
        tools.append(SearchConversationMemoryTool(ctx))
    if ctx.retriever and ctx.kb_namespaces:
        tools.append(KBSearchTool(ctx))
    return tools
