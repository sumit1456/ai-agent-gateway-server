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

    async def persist(self, run_id: str) -> None:
        """Flush all in-memory artifacts to the Artifact Postgres table."""
        if not self._items:
            return
        try:
            from app.db import SessionLocal
            from app.models.tables import Artifact
            async with SessionLocal() as db:
                for local_id, item in self._items.items():
                    db.add(Artifact(
                        run_id=run_id,
                        local_id=local_id,
                        kind=item["kind"],
                        summary=item["summary"],
                        content=item["content"],
                    ))
                await db.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to persist artifacts for run %s: %s", run_id, exc)

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