# AI Agent Gateway — Implementation Plan

## Conversation History Answer (read this first)

**Short answer: Conversation history is NOT currently sent to LLMs. Each run is stateless.**

Here's what actually happens today:

- Each run gets a **fresh set of messages** (`[SystemMessage, HumanMessage]`) — no prior turns
- `llm_utils.py` builds message lists per-call, never accumulating across runs
- `AgentSession.facts` exists (short bullet points, max 20) — this is the only cross-run memory
- Facts are injected into every prompt via `_facts(state)` in `views.py` — so durable findings carry over, but **the actual conversation (user messages + assistant replies) does not**

**What this means for your use case:**

If a user calls the gateway twice:
- Call 1: "Summarize the Q3 report"  → answer stored in `Run.result`
- Call 2: "Now compare it to Q2"     → agent has **no memory of Call 1** ❌

**Fix required: Conversation history must be loaded from prior runs and prepended to the system prompt or as prior messages.**

---

## Remaining Work — Priority Order

---

### BLOCKER 1 — Tool schemas are empty (LLM never calls tools)

**File**: `app/tools/registry.py`  
**Problem**: `schemas()` always returns `[]`. LangChain needs proper `StructuredTool` objects to bind to the LLM via `.bind_tools()`. Without this, the LLM has no tools available and just replies in plain text — no tool calling works at all.

**Fix**:
- Each `BaseTool` needs a `to_langchain()` method returning a `StructuredTool`
- `registry.schemas(tool_names)` must return those `StructuredTool` objects
- Requires adding input schemas to each tool (Pydantic models for args)

**Files to change**: `app/tools/base.py`, `app/tools/registry.py`, `app/tools/builtin.py`, `app/tools/webhook.py`, `app/tools/client.py`

---

### BLOCKER 2 — Run result never returned to caller

**File**: `app/api/runs.py`  
**Problem A**: `execute_run()` is called without `agent_id` and `goal` is never set (initial state has `"goal": ""`). The engine runs but processes an empty goal.  
**Problem B**: When the engine finishes, the `Run` DB record status never updates from `"running"` to `"done"`. `GET /runs/{run_id}` always returns `status: "running"` with empty result.  
**Problem C**: No SSE endpoint exists — `# SSE endpoint omitted` comment in the file.

**Fix — Two-part**:

**Part 1 — Fix run creation** (`runs.py`):
```python
# Pass agent_id, set goal from input
asyncio.create_task(execute_run(
    run_id=run_id,
    user_id=str(current_user.id),
    config_dict=config_dict,
    input_str=input,
    variables=variables or {},
    agent_id=str(agent.id),      # ← add this
    goal=input,                   # ← add this
))
```

**Part 2 — Update Run record when done** (`runner.py` → `run_graph()`):
```python
finally:
    await ctx.artifacts.persist(run_id)
    # Update Run record in DB
    async with SessionLocal() as db:
        run = await db.get(Run, uuid.UUID(run_id))
        if run:
            run.status = "done" if not error else "failed"
            run.result = {"answer": ctx._final_answer}
            run.stop_reason = final_stop_reason
            run.tokens_used = ctx.usage.total
            db.add(run)
            await db.commit()
    await ctx.sink.emit("run.done", {"run_id": run_id})
```

**Part 3 — Add SSE stream endpoint** (`runs.py`):
```
GET /v1/runs/{run_id}/stream
Content-Type: text/event-stream

Events emitted:
  run.started   → { run_id }
  answer.delta  → { text }         (streaming tokens)
  answer.reset  → {}               (discard partial text, tool call happened)
  answer.done   → { text }         (final complete answer)
  run.error     → { error }
  run.done      → { run_id }
```

**Polling alternative** (for simple clients):
```
GET /v1/runs/{run_id}
→ { status: "done", result: { answer: "..." }, tokens_used: 1234 }
```
Both should work. SSE for real-time clients, polling for simple integrations.

---

### BLOCKER 3 — Conversation history not sent to LLMs

**Problem**: Each run is completely stateless. Multi-turn conversations are broken.

**Fix — Load prior run messages into context**:

The approach: when starting a run, load the last N completed runs from the same session and inject them as prior messages.

```python
# In build_context() or as part of the system prompt:
prior_turns = await load_session_history(session_id, last_n=10)
# prior_turns = [
#   {"role": "user", "content": "Summarize Q3"},
#   {"role": "assistant", "content": "Q3 revenue was..."},
# ]
```

Two strategies (pick one or make configurable per agent):

**Strategy A — Inject as LangChain messages** (preferred):
Prepend prior `HumanMessage` / `AIMessage` pairs before the current user message in `direct_node` and `tool_loop`.

**Strategy B — Inject as context in system prompt**:
Summarize prior turns and append to `persona`. Cheaper tokens but loses structure.

**Storage**: `Run` table already has `input` (user message) and `result` JSONB (has `answer`). Just query last N done runs for the session.

**New field needed**: `AgentConfig.max_history_turns: int = 10`

---

### NICE TO HAVE (post-blocker)

#### 4 — Document upload endpoint needs multipart form
`POST /v1/knowledge-bases/{kb_id}/documents` currently takes `content: bytes` as a query param — unusable from any real client. Needs:
```python
from fastapi import UploadFile, File
async def upload_document(file: UploadFile = File(...), ...):
    content = await file.read()
```

#### 5 — KB delete should clean up Pinecone namespace
`DELETE /v1/knowledge-bases/{kb_id}` deletes the DB record but leaves all vectors in Pinecone.
Fix: call `retriever.delete_collection(kb.namespace)` before `db.delete(kb)`.

#### 6 — `/v1/knowledge-bases/{kb_id}/search` is still a stub
Wire it to `KnowledgeRetriever.search()` — useful for users to test KB independently.

#### 7 — Cancel run should actually cancel the engine
`POST /runs/{run_id}/cancel` sets DB status but never sets `ctx.cancel` event. The engine keeps running in the background.
Fix: `RUNS[run_id].cancel.set()` before updating DB.

#### 8 — `run_graph()` goal is always empty string
`initial_state["goal"]` is set to `""` — the engine runs on an empty goal. Must be set to `input_str`.

---

## What Pinecone Configures vs What's Still Needed

| If you configure Pinecone | What works |
|---|---|
| KB ingestion | ✅ Real embedding + upsert (fixed) |
| `kb_search` tool | ✅ Real vector search + rerank (fixed) |
| KB pre-fetch per step | ✅ Already wired in `tool_loop.prefetch_kb` |
| Agent runs without KB | ✅ Works fine — KB is optional |

**Pinecone is not a blocker for testing.** The 3 blockers above are all in the engine/API layer — independent of Pinecone.

---

## Test Sequence (once blockers 1-3 fixed)

```bash
# 1. Register
POST /auth/register  { email, username, password, name }

# 2. Get API key
POST /auth/api-keys  → { key: "gw_xxx" }   # show once, store it

# 3. Add provider (e.g. OpenRouter)
POST /v1/providers   { provider: "openrouter", api_key: "sk-or-..." }
→ { id: "provider-uuid" }

# 4. Create agent
POST /v1/agents  {
  name: "my-agent",
  provider_id: "provider-uuid",
  models: "meta-llama/llama-3.1-70b-instruct",
  system_prompt: "You are a helpful assistant.",
  limits: { force_mode: "direct" }   # skip planning for quick test
}
→ { id: "agent-uuid" }

# 5. Run it
POST /v1/agents/{agent-uuid}/run  
Body: { "input": "What is 2+2?", "variables": {} }
Header: Authorization: Bearer gw_xxx
→ { run_id: "run-uuid" }

# 6. Get result (polling)
GET /v1/runs/{run-uuid}
→ { status: "done", result: { answer: "4" } }

# OR stream
GET /v1/runs/{run-uuid}/stream
→ SSE events
```

---

## Summary of All Files Changed So Far

| File | What changed |
|---|---|
| `app/models/tables.py` | Added `AgentKV` table, documented `Artifact.kind` values |
| `app/engine/types.py` | Added `ArtifactKind` literal, `kind` field on `StepResult` |
| `app/engine/prompts.py` | Updated `EXECUTOR_SYSTEM` to include artifact kind instructions |
| `app/engine/context.py` | Added `ArtifactStore.persist()` — saves to Postgres at run end |
| `app/engine/state_ops.py` | `apply_result` passes `res.kind` to `artifacts.put` |
| `app/tools/builtin.py` | Fixed all stubs; added 5 new gateway tools (ctx-aware) |
| `app/knowledge/ingest.py` | Real ingestion pipeline (decode → split → embed → upsert) |
| `app/engine/runner.py` | Registers builtin + user tools; persists artifacts after run |
| `app/db.py` | Added `agent_kv` CREATE TABLE IF NOT EXISTS to `init_db()` |
