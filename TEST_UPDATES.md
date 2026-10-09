# Test Script Updates

## What Changed in `test_agent_run.py`

### 1. Added End-User Tracking
```python
# Before:
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# After:
END_USER_ID = "test_user_123"  # NEW

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "X-End-User-ID": END_USER_ID  # NEW: Tracks end-user
}
```

### 2. Enhanced Output
Now shows which end-user is making the request:
```
STEP 5: Run Agent (ID: xxx)
🎭 Simulating end-user: test_user_123
```

### 3. Added Bonus Test: Session Isolation
Automatically tests that different end-users have isolated sessions:

```python
# Test Flow:
1. Alice says: "My favorite color is blue"
2. Bob asks: "What is Alice's favorite color?"
3. Verify: Bob's agent should NOT know (sessions isolated)
```

**Expected Result:**
```
✅ Session isolation working correctly!
   Bob doesn't have access to Alice's conversation.
```

---

## How to Run

```bash
# Standard test (with end-user tracking)
python test_agent_run.py

# The script will:
# 1. List providers ✓
# 2. Check configured providers ✓
# 3. List models ✓
# 4. Create/find agent ✓
# 5. Run agent as "test_user_123" ✓
# 6. Poll for results ✓
# 7. BONUS: Test session isolation ✓
```

---

## Testing Multiple End-Users Manually

### Test 1: Create Session for User A
```bash
curl -X POST http://localhost:8000/v1/agents/{agent_id}/run \
  -H "Authorization: Bearer your_key" \
  -H "X-End-User-ID: user_alice" \
  -H "Content-Type: application/json" \
  -d '{"input": "Remember: I love pizza"}'
```

### Test 2: Check User B Cannot See User A's Data
```bash
curl -X POST http://localhost:8000/v1/agents/{agent_id}/run \
  -H "Authorization: Bearer your_key" \
  -H "X-End-User-ID: user_bob" \
  -H "Content-Type: application/json" \
  -d '{"input": "What food does Alice love?"}'
```

**Expected:** Bob's agent won't know ✅

### Test 3: Verify User A Remembers Their Own Data
```bash
curl -X POST http://localhost:8000/v1/agents/{agent_id}/run \
  -H "Authorization: Bearer your_key" \
  -H "X-End-User-ID: user_alice" \
  -H "Content-Type: application/json" \
  -d '{"input": "What food do I love?"}'
```

**Expected:** Agent responds "pizza" ✅

---

## What the Test Validates

### ✅ End-User Tracking
- Request includes `X-End-User-ID` header
- Session created/found for specific end-user
- Run recorded with `end_user_id`

### ✅ Session Isolation
- Alice's session ≠ Bob's session
- Conversation history isolated
- KV storage isolated
- Facts isolated

### ✅ Backward Compatibility
- If you remove `X-End-User-ID` from headers
- System uses `end_user_id = "default"`
- Still works! ✅

---

## Troubleshooting

### Issue: "end_user_id cannot be null"
**Fix:** Check `tables.py` has `default="default"`:
```python
end_user_id: str = Field(default="default", index=True)
```

### Issue: Sessions not isolating
**Check:** Are you passing different `X-End-User-ID` values?
```python
# Wrong
headers = {"X-End-User-ID": "alice"}  # Same for both
headers = {"X-End-User-ID": "alice"}

# Right
headers_alice = {"X-End-User-ID": "alice"}
headers_bob = {"X-End-User-ID": "bob"}
```

### Issue: Test hangs
**Cause:** Agent is still running
**Fix:** Increase timeout or check agent configuration

---

## Next Testing Steps

### 1. Test Conversation History Filters
```python
# In conversation, ask:
"What did we discuss yesterday?"
"Show me only my questions"
```

### 2. Test KV Storage Isolation
```python
# User A stores a value
"Remember my theme preference is dark mode"

# User B stores same key
"Remember my theme preference is light mode"

# Both should work independently ✅
```

### 3. Test Analytics
```sql
-- Check runs per end-user
SELECT end_user_id, COUNT(*) 
FROM runs 
GROUP BY end_user_id;
```

---

## Example Output

```
==============================================================
  STEP 5: Run Agent (ID: xxx)
==============================================================

🎭 Simulating end-user: test_user_123

Sending request to: POST /v1/agents/{agent_id}/run
Headers: Authorization, X-End-User-ID: test_user_123
Payload: {
  "input": "what was the last question i asked?"
}

Status: 200
{
  "run_id": "abc-123",
  "status": "running",
  "message": "Run started successfully."
}

✅ Run started successfully!
📌 Run ID: abc-123
👤 End-User: test_user_123
Status: running

==============================================================
  BONUS: Test Session Isolation
==============================================================

Testing that different end-users have isolated sessions...

👤 User: alice
   Input: 'My favorite color is blue'
   ✅ Run started: run-456

👤 User: bob
   Input: 'What is Alice's favorite color?'
   ✅ Run started: run-789

📝 Bob's Response:
   I don't have any information about Alice's favorite color...

✅ Session isolation working correctly!
   Bob doesn't have access to Alice's conversation.

==============================================================
  All Tests Complete!
==============================================================
```

---

## Summary

✅ **Updated test script with end-user tracking**  
✅ **Added session isolation test**  
✅ **Enhanced output for better visibility**  
✅ **Backward compatible (can omit X-End-User-ID)**

Your test script now properly validates the multi-tenant architecture! 🎉
