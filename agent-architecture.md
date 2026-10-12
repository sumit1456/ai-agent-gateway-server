# Agent Architecture

> Code-grounded architecture review of `ai-agent-gateway-server/app` (2026-10-10). This describes the current implementation, then lists gaps and recommended changes. A recommendation below is not necessarily implemented.

## At a glance

```mermaid
flowchart TD
    A[POST /agents/{agent_id}/run] --> B[Find or create AgentSession]
    B --> C[Create Run and persist user ConversationMessage]
    C --> D[Commit, then start background task]
    D --> E[Build RunContext: provider, tools, KB, session memory]
    E --> F[LangGraph router]
    F -->|direct| G[Direct ReAct tool loop]
    F -->|plan| H[Planner]
    H --> I[Executor: ready steps, bounded parallelism]
    I -->|more steps| I
    I --> J[Reviewer]
    J -->|retry/replan| H
    J -->|approved| K[Finalizer]
    G --> L[Final state]
    K --> L
    L --> M[Persist Run result and assistant ConversationMessage]
    M --> N[Occasional session-summary compaction]

    O[Document ingestion] --> P[Chunk and embed]
    P --> Q[Pinecone KB namespace]
    I -. optional kb_queries / kb_search .-> Q
```

The engine has two execution routes: a direct answer/tool route and a plan/execute/review/finalize route. Both are selected by the router. Conversation persistence and knowledge-base retrieval are separate systems: conversation turns are stored in Postgres; uploaded knowledge documents are embedded into Pinecone.

## 1. Run creation and durable records

The run API is `app/api/runs.py` (`create_run`). It:

1. Authenticates the caller and checks that the agent belongs to that account.
2. Finds or creates an `AgentSession` for the agent, `X-End-User-ID` (or `default`), and the optional caller-owned `external_session_id`. The ID can be supplied in the JSON body or as `X-Session-ID`; if both are present they must match.
3. Creates a UUID `run_id` and builds the runtime agent config from the saved agent config, with API-level defaults for models and limits.
4. Inserts a `Run` row and the original user message as a `ConversationMessage` linked to both the session and run.
5. Commits these records **before** starting `execute_run` as a background task.

The early commit closes the race where a fast run could finish before its `Run` row existed. The background task is still an in-process `asyncio` task, not a durable queue: a process restart can interrupt active work.

### Persistence model

| Record | Purpose | Current fields/behavior |
|---|---|---|
| `AgentSession` (`sessions`) | Groups a conversation for one agent, end user, and external session ID | `facts`, rolling `summary`, `summary_message_count`, compact `task_state` |
| `Run` (`runs`) | One request execution | `id` is the `run_id`; stores input, variables, status, result, stop reason, token count, duration, and per-node graph state snapshots |
| `ConversationMessage` (`conversation_messages`) | Ordered user/assistant transcript | Session ID, run ID, role, content, creation time |
| `Artifact` (`artifacts`) | Large intermediate/final step outputs | Run ID, local artifact ID, kind, summary, full content |

The API writes the user message before execution. At completion, the runner writes the final answer to `Run.result` and inserts an assistant message linked to the same `run_id` in the same DB transaction. If a run fails, the original user message and failed `Run` remain; the runner does not add an assistant message for a failed run. Artifacts are persisted separately by the run context.

`run_id` is the database `runs.id`; there is no separate `runner_id`. `Run.state` stores the graph checkpoint after each completed node. `AgentSession.task_state` is a smaller cross-run checklist, not a substitute for the full `Run.state` checkpoint.

## 2. Context loaded for a run

`app/engine/runner.py` builds a `RunContext` before invoking the graph. It loads:

- The configured model provider and decrypts its API key.
- The configured tools and built-in tools.
- Attached knowledge-base records and a `KnowledgeRetriever` when KB IDs resolve.
- The session summary and recent completed conversation turns.

Recent messages are loaded from Postgres, grouped by `run_id`, and selected as up to **five complete prior turns** (a user message plus an assistant message), then bounded to about 4,000 characters. Timestamps are preserved. Prompt views have per-role character budgets; these are conservative token approximations, not tokenizer-exact counts, and system prompts are separate from the user-view budget.

The summary is used for older context. After enough older messages accumulate, the runner makes a reviewer-model call to update a summary in batches, leaving the latest six messages (three turns) verbatim. This compaction happens after the main answer has been persisted. It is not vector retrieval, and its extra model-call duration is not reflected in the already-recorded `Run.duration_ms` or finalized trace duration.

### Role-specific prompt and context contract

Role templates and selectable behavior presets live in `app/engine/prompt_templates.py`. `get_prompt` composes the built-in role duty with the selected preset and per-agent additional instructions. The dashboard saves those selections and instructions in the agent configuration JSON. Advanced `*_prompt` fields remain full overrides.

- **Router:** receives the current goal, available tools, and KB availability. It does not receive prior turns.
- **Direct responder:** receives the current request, tools, shared agent persona, and at most four recent turns.
- **Planner:** receives the current goal, task ledger, facts, tools, KB availability, and replan state. It receives compact prior session context only when the request explicitly asks to continue or resume earlier work.
- **Executor:** receives the overall goal, its assigned step and criteria, dependencies/inputs, relevant facts, and KB results. It does not receive prior conversation turns.
- **Controller:** receives the current workflow state and state-derived allowed actions. It does not receive prior conversation turns.
- **Reviewer:** receives success criteria and completed step evidence. It does not receive prior conversation turns.
- **Finalizer:** receives the user request, task ledger, step results, facts, and shared agent persona. It does not receive prior conversation turns by default.

Prompt views have per-role character budgets; these are conservative token approximations, not tokenizer-exact counts. System prompt and tool-schema tokens are accounted for separately by the model/provider.

`get_conversation_history` is SQL-backed chronological history. `search_conversation_memory` is optional semantic retrieval over older turns. The latter is available only when an attached KB provides Pinecone credentials, and it filters on record type, user, agent, and exact session ID. Recent transcript remains sourced from Postgres.

## 3. Graph and execution stages

The graph is built in `app/engine/graph.py`; nodes live in `app/engine/nodes.py`.

### Router

The graph starts at `router`:

- `force_mode` can force `direct` or `plan`.
- If the router sees no visible tools and no KB, it chooses direct without an LLM call.
- Otherwise, a structured reviewer-role model call chooses direct or plan.

Built-in tools are registered for every run, and the router excludes only `read_artifact` from its visible list. That usually leaves visible tools even when the agent has no user-defined tools, so the “no tools → direct” shortcut may rarely apply; simple runs can still pay for a routing call.

### Direct route

The direct node runs a ReAct loop: model turn → execute requested tools concurrently → append tool results → model turn. The final allowed turn has tools disabled so the model must answer. Text can stream as `answer.delta`; if streamed text is superseded by a tool call/retry, the LLM layer emits `answer.reset`.

### Planned route

1. **Planner:** requests a structured `Plan`, validates step IDs, tool names and dependency cycles, then adds validated steps to graph state. A replan retains prior completed work and should add new steps.
2. **Executor:** finds pending steps whose dependencies are done and runs up to `max_parallel_steps` concurrently. A step can prefetch up to three planner-declared KB queries, then runs its own tool loop. Tool calls within a loop are also executed concurrently.
3. **Reviewer:** asks for structured verdicts (`ok`, `retry_step`, `replan`, `abort`). Retry/replan routes back to the planner; approval routes to the finalizer.
4. **Finalizer:** combines the request, completed step artifacts, facts, and stop reason into the user-facing answer.

Limits include maximum steps, parallel steps, graph iterations, tool turns, tokens per run, tool-output size, and timeout. Transient provider errors are retried with exponential backoff. Structured calls prefer native structured output, with a plain-JSON fallback/repair path.

## 4. Tools and intermediate data

Built-in tools include artifact read/list, knowledge search, session fact memory, key/value storage, and conversation-history lookup. Configured user tools are wrapped as webhook or client tools. The executor exposes the tools assigned to that plan step plus `read_artifact`; it adds `kb_search` when a KB retriever is available.

Step output is validated as a `StepResult`. `apply_result` stores full output in an in-memory per-run `ArtifactStore`, adds a short summary and facts to graph state, and assigns an artifact ID. The runner persists artifact rows before completing the run. This lets later steps read full prior output without placing every full artifact in every prompt.

## 5. Knowledge-base and vector-database path

### Current document flow

1. A knowledge base is created with a Pinecone index host/key and a namespace.
2. Document upload records a `KbDocument` and runs `app/knowledge/ingest.py`.
3. The document is decoded, split into 512-character chunks with 64-character overlap, embedded with the configured NVIDIA embedding model, and upserted to Pinecone. Metadata currently contains `text`, `source`, and `doc_id`.
4. During an agent run, the planner can declare `kb_queries`; the executor prefetches those queries. The executor can also call `kb_search`.
5. `KnowledgeRetriever.search` embeds the query, queries each supplied namespace, gathers top matches, and optionally reranks them.

### Confirmed wiring mismatch

Knowledge-base creation stores a namespace string in `KnowledgeBase.namespace`, and ingestion and run context now both use that stored namespace.

The KB record stores `embedding_model`, but `Embedder` currently uses the global `settings.embedding_model`; the per-KB model value is not passed through. Ingestion and query must use the same embedding model and vector dimensions.

The retriever is constructed using the first attached KB’s index credentials/host and then searches every attached KB namespace. That is consistent only if those KBs share the same user index, as intended. Conversation messages are stored in a separate namespace per agent, with metadata including `record_type`, `session_id`, `agent_id`, `user_id`, `end_user_id`, `run_id`, `message_id`, `role`, and ISO timestamp. Search applies strict user/agent/session metadata filters. Vector IDs are stable message UUIDs, so retries overwrite instead of duplicating. The SQL transcript remains canonical; current vector indexing is best-effort after a successful run, so an outbox/reconciliation job is still needed for guaranteed indexing.

### Conversation history vs semantic memory

These are complementary, not competing stores:

- Keep Postgres as the canonical, exact transcript and use it for the newest turns and chronological/date-filtered requests.
- Keep the session summary for durable facts/preferences from older dialogue.
- If older conversational recall needs semantic search, index completed turns as a **derived** Pinecone memory, isolated by session namespace or a strict `session_id` filter. Retrieve only a few older relevant turns for the current request, and deduplicate against recent SQL turns.
- The vector copy should be rebuildable from Postgres. A reliable implementation should persist an indexing job/outbox alongside the completed turn, then upsert with stable IDs so retries are idempotent.

The code stores completed conversation turns in Pinecone and exposes semantic retrieval as an optional tool. It only performs automatic semantic retrieval when the recent transcript had to be clipped by its size budget; otherwise the model can explicitly request older details with the tool. SQL remains the source of truth.

## 6. Run events, results, cancellation, and process lifetime

Run progress is emitted to the in-process event sink and, when Redis is available, published on `run:{run_id}`. The API exposes run polling and an SSE stream. The final answer is returned from `Run.result`; `run_completed` also carries output, token count, duration, and stop reason. Redis also holds short-lived, session-scoped conversation-history query results; cache failures fall back to Postgres, which remains the source of truth.

Active contexts/tasks are kept in in-process registries (`RUNS`/`BACKGROUND`). This supports cancellation and client-tool callbacks in a single process, but does not provide cross-worker coordination or recovery after a process restart. A durable job queue and shared run coordination would be needed for multi-worker production execution.

## 7. Important gaps to review

These are observations from the current code, not claims that the intended design requires them:

1. **Pinecone indexing reliability:** transcript upsert is best-effort after successful completion. Add a transactional outbox and reconciliation job if vector indexing must be guaranteed.
2. **Embedding model consistency:** the KB record stores `embedding_model`, but `Embedder` currently uses the global setting; ingestion and query must use the same embedding model and dimensions.
3. **Context budget approximation:** per-role caps use character counts and do not include system prompt/tool-schema tokens. Use provider tokenization/usage telemetry if strict hard limits are needed.
4. **Session task concurrency:** `AgentSession.task_state` represents one current task. Prevent concurrent plans per session or normalize tasks into a separate table if multiple active tasks per session are required.
5. **Planner validation fallback is not connected to the graph:** on invalid plans, planner returns `mode="direct"`, but the graph’s router edge is the only place that branches on `mode`; planner always leads to executor. The stated fallback therefore does not route to direct mode.
6. **Retry/review settings are partly declarative:** `MAX_STEP_RETRIES` and `Limits.llm_review` exist, but the node path does not use them to enforce step retry counts or disable LLM review. Reviewer verdicts can put a step back to pending without a clear retry cap.
7. **Failed-step handling:** reviewer constructs verdicts for completed steps only. If every step fails, it ends as unrecoverable; if only some fail, the final answer may proceed with unfinished steps rather than a targeted retry.
8. **Plan identity tracking:** the planner passes `plan_hashes` where `validate_plan` expects previously used step IDs, so duplicate IDs across replans may not be rejected as intended.
9. **Run recovery is not automatic:** `Run.state` is checkpointed after completed graph nodes, but no API/worker currently claims and resumes a stranded `running` run. A new request in the same external session can continue from the compact session checklist, but exact replay needs a resume endpoint and artifact rehydration.
10. **Summary accounting:** compaction runs after the run’s duration/trace summary is recorded, so its extra model usage and time are not fully reflected in those recorded values.
11. **In-process task lifecycle:** background tasks and cancellation state are process-local; crashes/restarts can strand a `running` run.

## 8. Suggested target request pipeline

```text
Authenticate; resolve/create session from agent + end-user + external session ID
        ↓
Persist Run + timestamped user message (commit)
        ↓
Load session summary + recent timestamped turns within bounded context
        ↓
Pass bounded history to router/direct/planner; executor/reviewer receive task inputs only
        ↓
If raw history was clipped, retrieve at most two older matching messages from Pinecone
        ↓
Run direct or plan/execute/review path
        ↓
Persist graph checkpoint after each completed node; persist result + assistant message
        ↓
Upsert completed turns to Pinecone with stable IDs; compact old SQL history into summary
```

This keeps exact history, compact long-term context, and semantic recall distinct. It also makes the agent’s context predictable: recent turns are always present, old details are retrieved only when relevant, and vector indexing can be retried without losing the canonical transcript.
