"""
Test script to see detailed pipeline logging
"""
import requests
import json
import time

# Configuration
API_KEY = "gw_vfTUvJo08plY-Gmaz5PcR0x71HII7ad-b-gGDJTKIBs"
BASE_URL = "http://localhost:8000"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

print("=" * 80)
print("  🧪 TESTING WITH DETAILED LOGS")
print("=" * 80)

# Get agents
r = requests.get(f"{BASE_URL}/v1/agents/", headers=headers)
agents = r.json()

if not agents:
    print("❌ No agents found. Please create an agent first.")
    exit(1)

agent_id = agents[0]["id"]
print(f"\n✅ Using agent: {agents[0]['name']} (ID: {agent_id})")

# Test 1: Simple question
print("\n" + "=" * 80)
print("TEST 1: Simple Question")
print("=" * 80)

payload = {
    "input": "What is 2 + 2?",
    "variables": {}
}

print(f"\n📤 Sending: {payload['input']}")
print(f"⏱️  Start time: {time.strftime('%H:%M:%S')}")

r = requests.post(
    f"{BASE_URL}/v1/agents/{agent_id}/run",
    headers=headers,
    json=payload
)

if r.status_code != 200:
    print(f"❌ Error: {r.status_code}")
    print(r.text)
    exit(1)

run_data = r.json()
run_id = run_data["run_id"]
print(f"✅ Run started: {run_id}")
print(f"\n📋 Check your server logs to see the detailed pipeline execution!")
print(f"   Look for emojis: 🚀 🤖 🔧 ⏱️  💬 🪙")

# Poll for results
print(f"\n🔄 Polling for results...")
for i in range(30):
    time.sleep(1)
    r = requests.get(f"{BASE_URL}/v1/runs/{run_id}", headers=headers)
    
    if r.status_code == 200:
        result = r.json()
        if result["status"] in ["done", "failed"]:
            print(f"\n✅ Run completed!")
            print(f"   Status: {result['status']}")
            print(f"   Tokens: {result['tokens_used']}")
            print(f"   Duration: {result['duration_ms']}ms")
            if result["status"] == "done":
                print(f"   Answer: {result['result'].get('answer', 'N/A')[:100]}...")
            break
else:
    print("\n⏱️  Timeout waiting for results")

print("\n" + "=" * 80)
print("  📊 LOG ANALYSIS TIPS")
print("=" * 80)
print("""
Look for these sections in your server logs:

1. 🚀 [ROUTER] - Shows routing decision and tools available
2. 💬 [DIRECT] - Direct answer mode execution
3. 🔄 [TOOL_LOOP] - Tool calling loop
4. 🤖 [LLM/...] - Individual LLM calls with timing
5. 🔧 [TOOL_LOOP] - Which tools were called
6. 🏁 [RUN_GRAPH] - Final summary with token breakdown

Token breakdown will show:
- Tokens per role (planner, executor, reviewer)
- Individual LLM call durations
- Tool execution times
- Total duration breakdown
""")
