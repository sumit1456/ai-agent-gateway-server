# Migration Guide: End-User Support

## Overview
This migration adds support for tracking end-users (customers of your subscribing companies).

## Schema Changes

### 1. AgentSession Table
**Added fields:**
- `user_id` (UUID, FK to users) - The company/organization that owns the agent
- `end_user_id` (str, required, indexed) - Identifier for the end-user from company's system
- `external_session_id` (str, optional, indexed) - Optional session ID from company's system
- `updated_at` (timestamp) - Track when session was last updated

**Added constraint:**
- Unique constraint on `(agent_id, end_user_id, external_session_id)` to prevent duplicates

### 2. Run Table
**Added field:**
- `end_user_id` (str, optional, indexed) - Which end-user made this request

### 3. AgentKV Table
**Changed:**
- `end_user_id` (str, optional, indexed) - Scope key-value pairs per end-user
- Updated unique constraint to `(agent_id, end_user_id, key)` instead of `(agent_id, key)`

## SQL Migration (Alembic)

```sql
-- Add columns to sessions table
ALTER TABLE sessions ADD COLUMN user_id UUID REFERENCES users(id);
ALTER TABLE sessions ADD COLUMN end_user_id VARCHAR NOT NULL DEFAULT 'anonymous';
ALTER TABLE sessions ADD COLUMN external_session_id VARCHAR;
ALTER TABLE sessions ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Create indexes
CREATE INDEX idx_sessions_end_user_id ON sessions(end_user_id);
CREATE INDEX idx_sessions_external_session_id ON sessions(external_session_id);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);

-- Add unique constraint
ALTER TABLE sessions ADD CONSTRAINT uq_agent_enduser_session 
  UNIQUE (agent_id, end_user_id, external_session_id);

-- Add column to runs table
ALTER TABLE runs ADD COLUMN end_user_id VARCHAR;
CREATE INDEX idx_runs_end_user_id ON runs(end_user_id);

-- Modify agent_kv table
ALTER TABLE agent_kv ADD COLUMN end_user_id VARCHAR;
CREATE INDEX idx_agent_kv_end_user_id ON agent_kv(end_user_id);

-- Drop old constraint and add new one
ALTER TABLE agent_kv DROP CONSTRAINT IF EXISTS agent_kv_agent_id_key_key;
ALTER TABLE agent_kv ADD CONSTRAINT uq_agent_enduser_key 
  UNIQUE (agent_id, end_user_id, key);

-- Backfill existing data (optional - set to NULL or 'legacy')
UPDATE sessions SET end_user_id = 'legacy' WHERE end_user_id IS NULL;
UPDATE sessions SET user_id = (SELECT user_id FROM agents WHERE agents.id = sessions.agent_id);
```

## API Changes Required

### 1. POST /v1/runs
**Request Headers:**
```
Authorization: Bearer <api_key>
X-End-User-ID: <end_user_identifier>  (NEW - required)
X-Session-ID: <session_identifier>     (NEW - optional)
```

**Example:**
```bash
curl -X POST https://your-gateway.com/v1/runs \
  -H "Authorization: Bearer agw_abc123..." \
  -H "X-End-User-ID: user_john_doe" \
  -H "X-Session-ID: conversation_xyz" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "uuid-here",
    "input": "Summarize this document",
    "session_id": "optional-legacy-field"
  }'
```

### 2. Session Lookup Logic
**Before:**
```python
session = db.query(AgentSession).filter(
    AgentSession.agent_id == agent_id
).first()
```

**After:**
```python
session = db.query(AgentSession).filter(
    AgentSession.agent_id == agent_id,
    AgentSession.end_user_id == end_user_id,
    AgentSession.external_session_id == external_session_id  # optional
).first()
```

## Data Model Explanation

```
Company (User Table)
  └─ Agent
      └─ Sessions (many)
          ├─ end_user_id: "user_1"  → Session for User 1
          ├─ end_user_id: "user_2"  → Session for User 2
          └─ end_user_id: "user_3"  → Session for User 3
```

### Key Relationships:
- **1 Company** has **N Agents**
- **1 Agent** has **N Sessions** (one per end-user)
- **1 Session** belongs to **1 end-user** of the company
- **Sessions are isolated** by `(agent_id, end_user_id, external_session_id)`

## Benefits

✅ **Isolation:** Each end-user has their own conversation history
✅ **Analytics:** Track usage per end-user for your customers
✅ **Billing:** Enable per-seat or per-user pricing models
✅ **Compliance:** Easier to handle GDPR/data deletion requests per end-user
✅ **Debugging:** Trace issues to specific end-users

## Example Usage Patterns

### Pattern 1: One Continuous Conversation
```python
# All requests from user_1 go to same session
headers = {
    "X-End-User-ID": "user_1",
    "X-Session-ID": None  # or omit
}
# → Creates/reuses session for user_1
```

### Pattern 2: Multiple Conversation Threads
```python
# User has multiple independent chats
headers = {
    "X-End-User-ID": "user_1",
    "X-Session-ID": "thread_123"  # Sales questions
}

headers = {
    "X-End-User-ID": "user_1", 
    "X-Session-ID": "thread_456"  # Support questions
}
# → Two separate sessions for same user
```

### Pattern 3: Anonymous/Guest Users
```python
# Company can use random IDs for non-authenticated users
headers = {
    "X-End-User-ID": "guest_abc123",
    "X-Session-ID": None
}
```

## Rollback Plan

If migration fails:
```sql
ALTER TABLE sessions DROP COLUMN IF EXISTS end_user_id;
ALTER TABLE sessions DROP COLUMN IF EXISTS external_session_id;
ALTER TABLE sessions DROP COLUMN IF EXISTS updated_at;
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS uq_agent_enduser_session;

ALTER TABLE runs DROP COLUMN IF EXISTS end_user_id;

ALTER TABLE agent_kv DROP COLUMN IF EXISTS end_user_id;
ALTER TABLE agent_kv DROP CONSTRAINT IF EXISTS uq_agent_enduser_key;
ALTER TABLE agent_kv ADD CONSTRAINT agent_kv_agent_id_key_key UNIQUE (agent_id, key);
```

## Next Steps

1. ✅ Update database schema (completed)
2. ⏳ Update API endpoints to accept end_user_id header
3. ⏳ Update session lookup logic in runner
4. ⏳ Update frontend to pass end_user_id
5. ⏳ Add analytics queries for per-user usage
6. ⏳ Update documentation for customers
