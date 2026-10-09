# AI Agent Gateway — Current Pipeline Documentation

**Last Updated**: October 8, 2026  
**Purpose**: Complete breakdown of how the gateway works end-to-end

---

## Table of Contents
1. [Request Flow Overview](#request-flow-overview)
2. [Authentication & Authorization](#authentication--authorization)
3. [Run Lifecycle](#run-lifecycle)
4. [LangGraph Engine Pipeline](#langgraph-engine-pipeline)
5. [Tool System](#tool-system)
6. [Knowledge Base Integration](#knowledge-base-integration)
7. [Conversation History](#conversation-history)
8. [Current Limitations & Missing Features](#current-limitations--missing-features)

---

## Request Flow Overview

```
User Request
    ↓
API Authentication (JWT / API Key)
    ↓
Agent Configuration Loaded
    ↓
Provider Credentials Decrypted
    ↓
Session Created/Retrieved
    ↓
Run Record Created (status: "running")
    ↓
Background Task: LangGraph Engine
    ↓
Results Persisted (status: "done"/"failed")
    ↓
Response (Polling / SSE Stream)
```

---

## Authentication & Authorization

### Current Implementation

**User Registration** (`POST /auth/register`)
- Creates user with bcrypt password hash
- Returns user ID and basic info

**Login** (`POST /auth/login`)
- Validates credentials
- Returns JWT token (expires in 7 days)
- Token structure: `{"sub": user_id, "exp": timestamp}`

**API Keys** (`POST /auth/api-keys`)
- Generates `gw_` prefixed keys
- Stored as SHA256 hash in DB
- Prefix shown to user for identification
- Used via `Authorization: Bearer gw_xxx` header

**Security Layer**:
- JWT tokens: HS256 signing with secret key
- API keys: SHA256 hashing (one-way)
- Provider API keys: Fernet symmetric encryption
- Per-endpoint authentication via FastAPI `Depends(current_user)`

### ⚠️ Missing / Flawed
- ❌ No rate limiting per user/key
- ❌ No key expiration or rotation
- ❌ No OAuth2/SSO support
- ❌ No audit logs for key usage
- ❌ No permission scopes (all keys have full access)
- ❌ No IP whitelisting

---

## Run Lifecycle

### 1. Run Creation (`POST /v1/agents/{agent_id}/run`)

**Input**:
```json
{
  "input": "What is 2+2?",
  "variables": {"var1": "value1"},
  "stream": false
}
```

**Process**:
1. Validate agent exists and belongs to user
2. Get or create `AgentSession` for this agent
3. Generate run UUID
4. Parse agent config into `AgentConfig` object
5. Create `Run` record in DB (status: "running")
6. Spawn background task: `execute_run()`
7. Return `{"run_id": "uuid", "status": "running"}`

### 2. Background Execution

**Pipeline**:
```
execute_run()
    ↓
build_context() — Load provider, KB, tools
    ↓
register builtin tools (gateway tools)
    ↓
register user tools (webhooks/client tools)
    ↓
run_graph() — LangGraph execution (see next section)
    ↓
persist artifacts to DB
    ↓
update Run record: status, result, tokens_used
    ↓
append to conversation_history in AgentSession
    ↓
emit run.done event
```

**Context Object** (`RunContext`):
- `run_id`, `user_id`, `agent_id`
- `config`: AgentConfig with models, limits, system prompt
- `persona`: Rendered Jinja2 system prompt
- `llm_factory`: Creates LLM instances per role (planner/executor/reviewer)
- `tools`: ToolRegistry with all available tools
- `retriever`: KnowledgeRetriever (if KB attached)
- `artifacts`: In-memory artifact store
- `usage`: Token usage tracker
- `sink`: EventSink for SSE streaming
- `cancel`: Cancellation event
- `metrics`: Error/retry counters

### 3. Result Retrieval

**Polling** (`GET /v1/runs/{run_id}`):
```json
{
  "id": "uuid",
  "status": "done",
  "result": {"answer": "The answer is 4"},
  "tokens_used": 150,
  "stop_reason": "completed",
  "duration_ms": 2500
}
```

**Streaming** (`GET /v1/runs/{run_id}/stream`):
- SSE endpoint (text/event-stream)
- Events:
  - `run.started` → `{"run_id": "..."}`
  - `answer.delta` → `{"text": "partial..."}` (streaming tokens)
  - `answer.reset` → `{}` (discard partial, tool call happened)
  - `answer.done` → `{"text": "final answer"}`
  - `run.error` → `{"error": "..."}`
  - `run.done` → `{"run_id": "..."}`

### ⚠️ Missing / Flawed
- ❌ SSE subscription mechanism not fully implemented (no actual event queue)
- ❌ No webhook callbacks for run completion
- ❌ Cancel endpoint doesn't actually cancel the engine (sets DB status only)
- ❌ No run priority queuing
- ❌ No partial result checkpointing
- ❌ Duration tracking not implemented

---

## LangGraph Engine Pipeline

### State Machine

```
              [ROUTER]
              /      \
       (direct)      (plan)
         /               \
    [DIRECT]          [PLANNER]
        |                 |
       END            [EXECUTOR] ←┐
                         |        |
                      (all done)  | (pending steps)
                         |        |
                     [REVIEWER]   |
                       /  |  \    |
              (replan) (done) (continue)
                   |     |        |
              [PLANNER]  |     ───┘
                      [FINALIZE]
                         |
                        END
```

### Node Responsibilities

#### 1. **ROUTER** (Decision Point)
**Purpose**: Decide execution mode

**Logic**:
```python
if forced_mode:
    return forced_mode
elif no_tools and no_kb:
    return "direct"  # Nothing to plan
else:
    ask LLM: "direct" or "plan"?
```

**Output**: `{"mode": "direct" | "plan"}`

#### 2. **DIRECT** (Single-Pass Execution)
**Purpose**: Answer simple queries without planning

**Process**:
1. Build prompt: `{persona}\n\n{DIRECT_SYSTEM}`
2. Run tool loop (max_tool_turns iterations)
3. Stream answer via `answer.delta` events
4. Return final answer

**Use cases**: 
- Math calculations
- Simple lookups
- Questions answerable with 1-2 tool calls

#### 3. **PLANNER** (Multi-Step Decomposition)
**Purpose**: Break complex goals into executable steps

**Input** (to LLM):
```
GOAL: {user input}
VARIABLES: {runtime vars}
AVAILABLE TOOLS: {tool names}
KNOWLEDGE BASE: available/not available
PROGRESS SO FAR: {completed steps}
REPLAN REASON: {why we're replanning}
DEAD ENDS: {failed approaches to avoid}
FACTS: {accumulated facts}
```

**Output** (structured):
```python
Plan(steps=[
    {
        "id": "s1",
        "goal": "Fetch user data from API",
        "depends_on": [],
        "inputs": [],
        "tools": ["webhook_get_user"],
        "kb_queries": [],
        "success_criteria": "User data retrieved with ID, name, email"
    },
    {
        "id": "s2", 
        "goal": "Format data into report",
        "depends_on": ["s1"],
        "inputs": ["s1"],
        "tools": [],
        "kb_queries": [],
        "success_criteria": "Markdown report with user details"
    }
])
```

**Validation**:
- Max steps enforced (default: 8)
- Tools must exist
- No duplicate step IDs
- No circular dependencies
- Plan hash prevents identical retries

**State Update**:
- Appends new steps (status: "pending")
- Adds plan hash to prevent loops

#### 4. **EXECUTOR** (Parallel Step Execution)
**Purpose**: Execute pending steps in parallel

**Process**:
1. Find ready steps: `status == "pending" AND all depends_on are done`
2. Take up to `max_parallel_steps` (default: 3)
3. For each step in parallel:
   - **Prefetch KB**: Run `kb_queries` if KB available
   - **Build view**: Compile context (goal, inputs, artifacts, facts, KB results)
   - **Run tool loop**: Mini ReAct loop (LLM ↔ tools for max_tool_turns)
   - **Parse result**: Extract JSON or repair with structured call
4. Apply results to state:
   - Update step status: "done" / "failed"
   - Store output as artifact if large
   - Merge facts into state
   - Track problems

**Tool Loop** (per step):
```
messages = [SystemMessage(system), HumanMessage(user)]
for turn in range(max_tool_turns):
    ai_message = llm.call(messages, tools=bound_tools)
    if no tool_calls:
        return ai_message.content  # Done
    messages.append(ai_message)
    # Execute tool calls in parallel
    results = await gather(*[call_tool(tc) for tc in ai_message.tool_calls])
    messages.extend([ToolMessage(result) for result in results])
# Final turn without tools (force answer)
return llm.call(messages, tools=None)
```

**Result Schema**:
```python
StepResult(
    status="done" | "failed",
    summary="Brief description (<=2 sentences)",
    output="Full result for next steps",
    kind="step_output|code|document|data|error_log",
    facts=["durable findings"],
    problems=["what went wrong"]
)
```

**Artifact Storage**:
- If output > `max_tool_output_chars` (4000), store as artifact
- Assign ID (a1, a2, ...) 
- Replace output with artifact reference
- Later steps call `read_artifact(id)` to retrieve

**Loop Logic**:
- If all steps done → go to REVIEWER
- If steps pending → stay in EXECUTOR (parallel execution continues)

#### 5. **REVIEWER** (Quality Control)
**Purpose**: Validate completed steps against success criteria

**Input** (to LLM):
```
OVERALL GOAL: {user request}
STEPS TO REVIEW:
[s1] goal: Fetch user data
     success_criteria: User data retrieved with ID, name, email
     status: done
     summary: Retrieved user John Doe
     output: {"id": 123, "name": "John", "email": "..."}
     problems: []
```

**Output** (structured):
```python
ReviewResult(verdicts=[
    StepVerdict(
        step_id="s1",
        verdict="ok",  # ok | retry_step | replan | abort
        reason="Success criteria met"
    )
])
```

**Actions**:
- **ok**: Step approved, proceed
- **retry_step**: Mark step as pending, stays in executor loop (max retries: 2)
- **replan**: Add to dead_ends, trigger replanning
- **abort**: Unrecoverable failure, end run

**Iteration Tracking**:
- `state.iteration += 1` each review pass
- Max iterations: 5 (prevents infinite loops)

#### 6. **FINALIZE** (Answer Synthesis)
**Purpose**: Compose final user-facing answer from step results

**Input** (to LLM):
```
USER REQUEST: {original goal}
RUN STOPPED EARLY: {stop reason if any}
STEP RESULTS:
[s1] Fetch user data
     {"id": 123, "name": "John", ...}

[s2] Format report
     # User Report
     Name: John Doe
     ...

FACTS:
- User ID is 123
- Email verified
```

**Output**: Plain text answer (no JSON, no internal details)

**Streaming**: 
- Answer emitted via `answer.done` event
- No delta streaming in finalize (too late)

---

## Tool System

### Tool Architecture

```
BaseTool (abstract)
    ↓
    ├── BuiltinTool (gateway-provided, ctx-aware)
    │   ├── ReadArtifactTool
    │   ├── ListArtifactsTool
    │   ├── KBSearchTool
    │   ├── RememberFactTool
    │   ├── GetSessionMemoryTool
    │   ├── StoreKVTool
    │   ├── GetKVTool
    │   └── GetConversationHistoryTool
    │
    ├── WebhookTool (HTTP POST to external endpoint)
    └── ClientTool (execution delegated to client via callback)
```

### Tool Registration Flow

```
build_context()
    ↓
registry = ToolRegistry()
    ↓
register builtin tools (always available)
    ↓
load user tools from DB (Tool table)
    ↓
wrap as WebhookTool / ClientTool
    ↓
registry.register(tool)
```

### Tool Calling Mechanism

**LangChain Integration**:
1. `BaseTool.to_langchain()` → `StructuredTool`
2. `registry.schemas(tool_names)` → list of `StructuredTool`
3. `llm.bind_tools(schemas)` → LLM with tool calling enabled
4. LLM returns `AIMessage` with `tool_calls` array
5. `call_tool(name, args)` → execute and return `ToolResult`

**Tool Call Structure**:
```python
{
    "name": "kb_search",
    "args": {"query": "user authentication", "top_k": 5},
    "id": "call_abc123"  # LangChain generated
}
```

**Tool Result**:
```python
ToolResult(
    ok=True,
    output="[search results...]",
    error=None
)
```

### Builtin Tools (Always Available)

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `read_artifact` | Read artifact content | `artifact_id: str` | Full artifact dict |
| `list_artifacts` | List all artifacts | None | Formatted list |
| `kb_search` | Search knowledge base | `query: str, top_k: int` | Top chunks with scores |
| `remember_fact` | Store durable fact | `fact: str` | Confirmation |
| `get_session_memory` | Retrieve session facts | None | List of facts |
| `store_kv` | Persist agent KV pair | `key: str, value: Any` | Confirmation |
| `get_kv` | Retrieve KV value | `key: str` | Stored value or null |
| `get_conversation_history` | Get previous messages | `last_n: int` | Formatted conversation |

### User-Defined Tools

**Webhook Tools** (`kind: "webhook"`):
- POST request to `endpoint_url`
- Body: `{"arg1": "value1", ...}` (tool args as JSON)
- Headers: `Authorization: {auth_enc}` (decrypted)
- Timeout: `timeout_ms` (default: 10000)
- Response: Expects JSON, returned as tool output

**Client Tools** (`kind: "client"`):
- Execution delegated to client application
- Gateway creates async future
- Client calls `POST /runs/{run_id}/tool-result` to resolve
- Timeout: 30s
- **⚠️ Not fully implemented** (broker mechanism stubbed)

### Tool Caching

**Signature-based caching**:
- Key: `f"{tool_name}:{json.dumps(args, sort_keys=True)}"`
- Only successful results cached (`ok=True`)
- Scope: Per run (cleared after run completes)
- Benefits: Avoid redundant API calls, faster retries

### ⚠️ Missing / Flawed
- ❌ Client tools broker not implemented (futures never resolved)
- ❌ No tool timeout enforcement (relies on httpx/asyncio defaults)
- ❌ No tool call tracing/logging
- ❌ No tool usage quotas per agent
- ❌ No tool output sanitization (XSS risk if output shown in UI)
- ❌ Webhook auth only supports header-based (no OAuth)
- ❌ No tool versioning
- ❌ Cache grows unbounded during run (could OOM on many calls)

---

## Knowledge Base Integration

### Architecture

```
KnowledgeBase (DB table)
    ↓
Documents uploaded
    ↓
Ingestion Pipeline:
    1. Decode (detect charset)
    2. Split (recursive text splitter, 500 chars, overlap 50)
    3. Embed (OpenAI/Cohere/custom)
    4. Upsert to Pinecone (namespace = kb_id)
    ↓
Retriever:
    1. Query → Embed
    2. Pinecone search (top_k=20)
    3. Rerank (Cohere bge-reranker-v2-m3)
    4. Return top final_k (default: 5)
```

### Pinecone Schema

**Namespace**: Each KB gets unique namespace (`str(kb.id)`)

**Vector Structure**:
```python
{
    "id": "doc_uuid#chunk_0",
    "values": [0.123, ...],  # embedding vector
    "metadata": {
        "text": "chunk content",
        "source": "filename.pdf",
        "chunk_index": 0
    }
}
```

### Usage in Engine

**Two integration points**:

1. **Pre-fetch** (Planner declares KB queries):
   ```python
   step = {
       "kb_queries": ["user authentication", "password reset"]
   }
   ```
   - Executor runs queries before step
   - Results injected into step prompt (first 3000 chars)
   - No explicit tool call needed

2. **On-demand** (Agent calls `kb_search` tool):
   ```python
   kb_search(query="how to reset password", top_k=5)
   ```
   - Full control over query and result count
   - Results include scores and sources

### Configuration

**Per Knowledge Base**:
- `embedding_model`: Model name (e.g., "text-embedding-3-small")
- `provider_id`: Links to Provider (must support embeddings)
- `vector_api_key_enc`: Optional custom Pinecone key
- `vector_index_host`: Pinecone index host URL
- `rerank_model`: Reranker model (default: "bge-reranker-v2-m3")

### ⚠️ Missing / Flawed
- ❌ Document deletion doesn't clean up Pinecone vectors
- ❌ KB deletion doesn't delete Pinecone namespace
- ❌ No incremental updates (re-upload = duplicate vectors)
- ❌ No document status tracking UI (user can't see progress)
- ❌ Chunk overlap hard-coded (not configurable)
- ❌ No support for structured data (tables, JSON)
- ❌ No OCR for images/scanned PDFs
- ❌ Search endpoint `/v1/knowledge-bases/{kb_id}/search` stubbed
- ❌ No semantic deduplication (similar chunks both indexed)
- ❌ No metadata filtering in search (can't filter by source/date)

---

## Conversation History

### Storage Model

**Table**: `AgentSession.conversation_history` (JSONB)

**Structure**:
```python
[
    {
        "role": "user",
        "content": "What is 2+2?",
        "timestamp": "2026-10-08T10:00:00Z"
    },
    {
        "role": "assistant", 
        "content": "2+2 equals 4",
        "timestamp": "2026-10-08T10:00:05Z"
    }
]
```

### Persistence Flow

```
run_graph() completes
    ↓
Extract final_answer from state
    ↓
Load AgentSession by session_id
    ↓
Append user message: {"role": "user", "content": ctx.input, ...}
    ↓
Append assistant message: {"role": "assistant", "content": final_answer, ...}
    ↓
Save session to DB
```

### Retrieval (Opt-In)

**Default behavior**: History NOT sent to LLM (stateless)

**Agent-initiated retrieval**:
```python
# Agent calls tool when it needs context
result = get_conversation_history(last_n=10)
```

**Output format**:
```
[USER] What is 2+2?

[ASSISTANT] 2+2 equals 4

[USER] What if I add 2 more?

[ASSISTANT] That would be 6
```

### Design Rationale

**Why opt-in?**
- Prevents token bloat (no auto-injection)
- Agent decides when context is needed
- Cost-effective (most queries don't need history)
- Scales to long conversations (no exponential context growth)

### ⚠️ Missing / Flawed
- ❌ No conversation branching (one linear history per session)
- ❌ No conversation pruning (history grows indefinitely)
- ❌ No semantic search over history
- ❌ No conversation summarization
- ❌ Timestamps stored but not filterable
- ❌ No user-initiated history reset/clear
- ❌ Tool messages not stored (only user/assistant)
- ❌ No conversation export

---

## Current Limitations & Missing Features

### 🔴 Critical Issues

1. **Client Tools Broken**
   - `ClientTool` broker mechanism not implemented
   - Futures never resolved, always timeout
   - Blocks any client-side tool execution

2. **SSE Streaming Incomplete**
   - `EventSink.subscribe()` doesn't exist
   - SSE endpoint returns empty stream or errors
   - No actual event queue wiring

3. **Cancel Doesn't Work**
   - `POST /runs/{run_id}/cancel` sets DB status only
   - Engine continues running (never checks `ctx.cancel`)
   - Wastes resources on cancelled runs

4. **No Error Recovery**
   - Transient LLM errors may kill entire run
   - No automatic retry at run level
   - Partial progress lost on crash

5. **Missing Session-Run Link**
   - `execute_run()` doesn't pass `session_id` to context
   - `get_conversation_history` will fail (session_id always None)
   - Session memory tools broken

### 🟡 High Priority

6. **No Rate Limiting**
   - Users can spam runs
   - Provider quotas can be exhausted
   - No per-user, per-agent, or per-key limits

7. **Document Upload UI/UX**
   - No multipart form upload (uses raw bytes in query param)
   - No upload progress tracking
   - No file type validation

8. **Knowledge Base Cleanup**
   - Deleting KB doesn't delete Pinecone vectors
   - Deleting documents doesn't delete vectors
   - Namespace pollution over time

9. **Token Tracking Incomplete**
   - `duration_ms` never set
   - Cost calculation missing (tokens * price per provider)
   - No breakdown by step/role

10. **Artifacts Not Accessible**
    - No API endpoint to list/download artifacts
    - Stored in DB but no way to retrieve post-run
    - Useful for debugging and auditing

### 🟢 Nice to Have

11. **Observability Gaps**
    - No structured logging
    - No OpenTelemetry tracing
    - No run replay/debugging tools
    - Metrics counters not exposed (Prometheus?)

12. **Agent Sharing/Templates**
    - No agent marketplace/templates
    - Can't clone or share agent configs
    - No versioning

13. **Advanced Planning**
    - No conditional branches (if X then Y else Z)
    - No loops/iteration
    - No dynamic step generation
    - Human-in-the-loop not supported

14. **Knowledge Base Features**
    - No document versioning
    - No incremental updates
    - No metadata filtering
    - No hybrid search (keyword + semantic)

15. **Conversation Features**
    - No threading/branching
    - No regeneration of responses
    - No conversation search
    - No conversation analytics

16. **Provider Features**
    - No fallback providers (if primary fails)
    - No load balancing across keys
    - No automatic model selection
    - Provider quotas not tracked

17. **Security Hardening**
    - No content filtering (malicious prompts)
    - No PII detection/redaction
    - No audit trail
    - Webhook URLs not validated (SSRF risk)

18. **Multi-Tenancy**
    - No organizations/teams
    - No role-based access control
    - No resource quotas per org
    - Agents not shareable within team

19. **Testing/Validation**
    - No agent testing framework
    - No dry-run mode
    - No cost estimation before run
    - No output validation schemas

20. **Deployment/Scaling**
    - Single-process only (RUNS dict in memory)
    - No horizontal scaling
    - No job queue (Redis/Celery)
    - No graceful shutdown (in-flight runs lost)

### 🔧 Code Quality Issues

21. **Error Handling**
    - Many bare `except Exception` blocks
    - Errors swallowed with print statements
    - No custom exception hierarchy

22. **Type Safety**
    - Many `dict` types instead of typed models
    - Optional fields without proper defaults
    - Inconsistent UUID handling (str vs UUID)

23. **Testing**
    - No unit tests
    - No integration tests
    - No load tests
    - Critical paths untested

24. **Documentation**
    - No API documentation (Swagger/OpenAPI)
    - No deployment guide
    - No provider setup guides
    - No tool development guide

---

## Performance Bottlenecks

1. **Sequential Step Execution**
   - Only `max_parallel_steps` execute at once
   - Deep dependency chains are slow
   - No predictive scheduling

2. **Knowledge Base Prefetch**
   - Runs for every step (even if not needed)
   - No caching across steps
   - Rerank is expensive

3. **Artifact Storage**
   - Large artifacts stored in Postgres (slow)
   - No compression
   - Should use object storage (S3)

4. **Tool Call Overhead**
   - Each tool call waits for LLM round-trip
   - No tool call batching
   - No speculative execution

5. **Database Queries**
   - Many N+1 queries
   - No query optimization
   - No connection pooling tuning

---

## Recommended Priorities

### Phase 1: Fix Critical Bugs
1. Fix session_id propagation → conversation history works
2. Implement SSE event queue → streaming works
3. Fix cancel mechanism → runs can be stopped
4. Add rate limiting → prevent abuse

### Phase 2: Production Readiness
5. Structured logging + tracing
6. Document upload multipart form
7. KB cleanup on delete
8. Error recovery & retries
9. Graceful shutdown

### Phase 3: Scale & Optimize
10. Job queue (Redis)
11. Horizontal scaling
12. Artifact object storage
13. Tool call caching across runs
14. Provider failover

### Phase 4: Features & UX
15. Agent templates
16. Run replay/debugging
17. Conversation management
18. Advanced planning (conditionals, loops)
19. Multi-tenancy

---

## Conclusion

**What Works Well**:
✅ Core LangGraph pipeline (router → planner → executor → reviewer → finalize)  
✅ Tool system architecture (LangChain integration)  
✅ Knowledge base ingestion and retrieval  
✅ Multi-model support via provider abstraction  
✅ Conversation history storage (opt-in design)  

**Critical Gaps**:
❌ SSE streaming not wired up  
❌ Client tools not functional  
❌ Cancel doesn't work  
❌ Session_id not propagated  
❌ No rate limiting or quotas  

**Architecture is solid** — main issues are incomplete implementations and missing production features. With the bug fixes above, the gateway is ready for MVP deployment.
