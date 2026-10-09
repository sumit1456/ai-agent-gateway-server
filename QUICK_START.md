# 🚀 Quick Start: End-User Support

## Installation

```bash
# Install new dependency
pip install -r requirements.txt

# Start the app (database auto-updates)
python -m uvicorn app.main:app --reload
```

✅ That's it! No manual migration needed.

---

## Usage Examples

### 1. Basic Request (Backward Compatible)
```bash
curl -X POST http://localhost:8000/v1/agents/{agent_id}/run \
  -H "Authorization: Bearer your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Hello, how are you?"
  }'
```
**Result**: Uses `end_user_id = "default"`

---

### 2. With End-User Tracking (Recommended)
```bash
curl -X POST http://localhost:8000/v1/agents/{agent_id}/run \
  -H "Authorization: Bearer your_api_key" \
  -H "X-End-User-ID: user_john_123" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "My name is John and I love pizza"
  }'
```
**Result**: Creates isolated session for `user_john_123`

---

### 3. Test Session Isolation
```bash
# User A says something
curl -X POST ... -H "X-End-User-ID: alice" \
  -d '{"input": "Remember: my favorite color is blue"}'

# User B asks about User A
curl -X POST ... -H "X-End-User-ID: bob" \
  -d '{"input": "What is Alice favorite color?"}'
```
**Result**: Bob's agent won't know - sessions are isolated ✅

---

### 4. Test Conversation History Filters
```bash
# Have a conversation
curl -X POST ... -H "X-End-User-ID: alice" \
  -d '{"input": "I bought a Tesla yesterday"}'

# Later, ask about past
curl -X POST ... -H "X-End-User-ID: alice" \
  -d '{"input": "What did we discuss yesterday?"}'
```
**Result**: Agent uses `get_conversation_history(from_date="...")` automatically ✅

---

## For Your Customers (B2B Integration)

### Example: PDF Editor Company Integrates Your Gateway

**Their Backend Code (Node.js):**
```javascript
// PDF Editor's server-side code
app.post('/api/ai-assist', async (req, res) => {
  const userId = req.user.id; // Their authenticated user
  const question = req.body.question;
  
  const response = await fetch('https://your-gateway.com/v1/agents/agent_id/run', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${process.env.AGENT_GATEWAY_API_KEY}`, // Secret!
      'X-End-User-ID': userId, // Pass their user ID
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ input: question })
  });
  
  const result = await response.json();
  res.json(result);
});
```

**Their Frontend (React):**
```javascript
// Their users never see the API key
const askAI = async (question) => {
  const response = await fetch('/api/ai-assist', { // Their backend
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question })
  });
  return response.json();
};
```

---

## Built-in Tool Examples

### 1. Store User Preferences
Agent automatically uses `store_kv` (scoped per end-user):
```
User: "Remember I prefer dark mode"
Agent: [calls store_kv(key="theme_preference", value="dark")]
```

Later:
```
User: "What's my preference?"
Agent: [calls get_kv(key="theme_preference")] → "dark"
```

### 2. Search Conversation History
```
User: "What did we discuss about invoices last week?"
Agent: [calls get_conversation_history(
  from_date="2024-01-08",
  to_date="2024-01-15"
)]
```

### 3. Filter by Role
```
User: "Show me only my questions from yesterday"
Agent: [calls get_conversation_history(
  from_date="2024-01-14",
  role="user"
)]
```

---

## Analytics Queries

### Track Usage Per End-User
```sql
-- Total tokens per end-user for a company
SELECT 
  end_user_id,
  COUNT(*) as request_count,
  SUM(tokens_used) as total_tokens,
  AVG(duration_ms) as avg_duration_ms
FROM runs
WHERE user_id = 'company_uuid'
  AND created_at >= NOW() - INTERVAL '30 days'
GROUP BY end_user_id
ORDER BY total_tokens DESC;
```

### Most Active End-Users
```sql
SELECT 
  end_user_id,
  COUNT(*) as conversations,
  MAX(created_at) as last_active
FROM sessions
WHERE user_id = 'company_uuid'
GROUP BY end_user_id
ORDER BY conversations DESC
LIMIT 10;
```

### Revenue Attribution
```sql
-- If you charge per token
SELECT 
  user_id as company,
  SUM(tokens_used) * 0.0001 as estimated_cost_usd
FROM runs
WHERE created_at >= DATE_TRUNC('month', NOW())
GROUP BY user_id;
```

---

## Troubleshooting

### ❌ Error: "Agent ID not set on context"
**Fix**: Make sure `agent_id` is passed to `execute_run()` in runs.py
```python
# Should have:
await execute_run(..., agent_id=str(agent.id))
```

### ❌ Error: "end_user_id cannot be null"
**Fix**: Check `tables.py` - should have `default="default"`
```python
end_user_id: str = Field(default="default", index=True)
```

### ❌ Sessions not isolating
**Check**: Are you passing different `X-End-User-ID` values?
```bash
# Wrong - same end_user_id
curl ... -H "X-End-User-ID: user1"
curl ... -H "X-End-User-ID: user1"  # Same session!

# Right - different end_user_id  
curl ... -H "X-End-User-ID: user1"
curl ... -H "X-End-User-ID: user2"  # Different session ✅
```

---

## Security Best Practices

### ✅ DO:
- Keep API keys secret (server-side only)
- Use HTTPS in production
- Validate end_user_id format (prevent injection)
- Rate limit per end-user if needed
- Log access for auditing

### ❌ DON'T:
- Expose API keys in frontend code
- Send PII as end_user_id (use opaque IDs)
- Allow end-users to specify arbitrary end_user_id
- Store sensitive data in conversation history

---

## Migration Path for Existing Data

If you have existing sessions without `end_user_id`:

```sql
-- They'll automatically use "default" as end_user_id
-- No action needed!

-- Optional: Rename for clarity
UPDATE sessions 
SET end_user_id = 'legacy_user' 
WHERE end_user_id = 'default';
```

---

## Performance Tips

### Use Indexes (Already Done ✅)
```sql
-- Auto-created by SQLModel
CREATE INDEX idx_sessions_end_user_id ON sessions(end_user_id);
CREATE INDEX idx_runs_end_user_id ON runs(end_user_id);
```

### Cache Sessions (Future Enhancement)
```python
# Redis cache for hot sessions
redis.set(f"session:{agent_id}:{end_user_id}", session_data, ex=3600)
```

### Pagination for History
```python
# When conversation_history gets large
get_conversation_history(last_n=50)  # Don't load all
```

---

## What's Next?

### Phase 1: Core (✅ Done)
- [x] End-user tracking
- [x] Session isolation
- [x] Conversation history filters
- [x] KV scoping per user

### Phase 2: Analytics (Coming Soon)
- [ ] Customer dashboard (usage per end-user)
- [ ] Cost attribution
- [ ] Rate limiting per end-user

### Phase 3: Advanced (Future)
- [ ] Multi-session support (thread_id)
- [ ] Session export API
- [ ] GDPR compliance tools (delete end-user data)

---

## Support

**Documentation**: See `SETUP_COMPLETE.md` for full details  
**Migration**: See `MIGRATION_END_USER_SUPPORT.md` for SQL scripts  
**Changelog**: See `CHANGELOG_FIXES.md` for all changes

---

## TL;DR

```bash
# 1. Install
pip install -r requirements.txt

# 2. Start
python -m uvicorn app.main:app --reload

# 3. Use
curl -X POST http://localhost:8000/v1/agents/{id}/run \
  -H "Authorization: Bearer key" \
  -H "X-End-User-ID: user123" \
  -d '{"input": "Hello"}'
```

**That's it! 🎉**
