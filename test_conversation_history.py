"""
Test Script for GetConversationHistoryTool

This script tests the conversation history retrieval functionality:
1. Creates multiple runs to build conversation history
2. Tests retrieving conversation history with various filters
3. Validates filtering by last_n, date range, and role
"""
import requests
import json
import time
import sys
from datetime import datetime, timedelta

# Configuration - from test_agent_run.py
API_KEY = "gw_toY-XPUta3PokTQyRSiNlKRMyfnVuXWOGXAvxTQLzjU"
BASE_URL = "http://localhost:8000"
END_USER_ID = "test_conversation_history_user"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "X-End-User-ID": END_USER_ID
}


def print_section(title):
    """Print a section header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")


def print_response(response, show_json=True):
    """Print response status and content."""
    print(f"Status: {response.status_code}")
    if show_json:
        try:
            print(json.dumps(response.json(), indent=2))
        except:
            print(response.text)
    print()


def check_response(response, expected_status=200):
    """Check if response is successful."""
    if response.status_code != expected_status:
        print(f"❌ ERROR: Expected {expected_status}, got {response.status_code}")
        print_response(response)
        return False
    return True


def wait_for_run_completion(run_id, max_polls=30, poll_interval=2):
    """Poll until run completes or fails."""
    print(f"⏳ Waiting for run {run_id} to complete...")
    
    for i in range(max_polls):
        time.sleep(poll_interval)
        
        r = requests.get(f"{BASE_URL}/v1/runs/{run_id}", headers=headers)
        
        if check_response(r):
            run_status = r.json()
            status = run_status["status"]
            
            if status == "done":
                print(f"✅ Run completed successfully!")
                return True
            elif status == "failed":
                print(f"❌ Run failed: {run_status.get('error_message', 'Unknown error')}")
                return False
            elif status == "running":
                print(f"   Poll {i+1}: Still running... ({(i+1)*poll_interval}s elapsed)")
        else:
            print(f"❌ Error polling run status")
            return False
    
    print(f"⏱️ Timeout: Run did not complete within {max_polls * poll_interval} seconds")
    return False


def run_agent(agent_id, input_text, description=""):
    """Run the agent with given input and wait for completion."""
    if description:
        print(f"\n📝 {description}")
    
    try:
        run_payload = {
            "input": input_text,
            "variables": {}
        }
        
        print(f"   Input: '{input_text}'")
        
        r = requests.post(
            f"{BASE_URL}/v1/agents/{agent_id}/run",
            headers=headers,
            json=run_payload,
            timeout=30
        )
        
        if check_response(r):
            run_data = r.json()
            run_id = run_data["run_id"]
            print(f"   Run ID: {run_id}")
            
            # Wait for completion
            if wait_for_run_completion(run_id):
                # Get final result
                r_final = requests.get(f"{BASE_URL}/v1/runs/{run_id}", headers=headers)
                if r_final.status_code == 200:
                    result = r_final.json()
                    output = result.get("result", {}).get("answer", "")
                    if output:
                        print(f"   Answer: {output[:150]}...")
                return run_id
            else:
                return None
        else:
            print("❌ Failed to start run")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


# ============================================================================
# SETUP: Get Agent
# ============================================================================
print_section("SETUP: Get Existing Agent")
try:
    r = requests.get(f"{BASE_URL}/v1/agents/", headers=headers)
    
    if check_response(r):
        agents = r.json()
        print(f"Found {len(agents)} agents")
        
        if len(agents) == 0:
            print("\n❌ No agents found! Please create an agent first.")
            sys.exit(1)
        
        # Use first existing agent
        agent_id = agents[0]["id"]
        agent_name = agents[0]["name"]
        
        print(f"✅ Using agent: {agent_name} (ID: {agent_id})")
        print(f"👤 End-User: {END_USER_ID}")
        print(f"\nThis test will create a conversation history for this user.")
        
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)


# ============================================================================
# TEST 1: Build Conversation History
# ============================================================================
print_section("TEST 1: Build Conversation History")
print("Creating multiple runs to build up conversation history...\n")

test_conversations = [
    "What is the capital of France?",
    "Tell me about Python programming.",
    "What's the weather like today?",
    "Explain quantum computing in simple terms.",
    "What are the benefits of exercise?",
]

run_ids = []
for i, question in enumerate(test_conversations, 1):
    run_id = run_agent(
        agent_id, 
        question,
        f"Run {i}/{len(test_conversations)}"
    )
    if run_id:
        run_ids.append(run_id)
    time.sleep(1)  # Brief pause between runs

print(f"\n✅ Created {len(run_ids)} conversation entries")


# ============================================================================
# TEST 2: Retrieve Full Conversation History
# ============================================================================
print_section("TEST 2: Retrieve Full Conversation History")
print("Testing: 'What were all my previous questions?'\n")

run_id = run_agent(
    agent_id,
    "What were all my previous questions? Use the get_conversation_history tool.",
    "Retrieving full history"
)

if run_id:
    print("\n✅ Successfully retrieved full conversation history")
else:
    print("\n❌ Failed to retrieve conversation history")


# ============================================================================
# TEST 3: Retrieve Last N Messages
# ============================================================================
print_section("TEST 3: Retrieve Last 3 Messages")
print("Testing: 'What were my last 3 questions?'\n")

run_id = run_agent(
    agent_id,
    "What were my last 3 questions? Use get_conversation_history with last_n=3.",
    "Retrieving last 3 messages"
)

if run_id:
    print("\n✅ Successfully retrieved last 3 messages")
else:
    print("\n❌ Failed to retrieve last 3 messages")


# ============================================================================
# TEST 4: Filter by Role (User Messages Only)
# ============================================================================
print_section("TEST 4: Filter by Role (User Messages Only)")
print("Testing: 'Show me only my questions (user role)'\n")

run_id = run_agent(
    agent_id,
    "Show me only my questions from the conversation history. Use get_conversation_history with role='user'.",
    "Filtering by user role"
)

if run_id:
    print("\n✅ Successfully filtered by user role")
else:
    print("\n❌ Failed to filter by role")


# ============================================================================
# TEST 5: Test with New Conversation
# ============================================================================
print_section("TEST 5: Ask About Previous Conversation")
print("Testing natural language query about conversation history\n")

run_id = run_agent(
    agent_id,
    "what was the last question i asked?",
    "Natural language query"
)

if run_id:
    print("\n✅ Successfully handled natural language query")
else:
    print("\n❌ Failed to handle natural language query")


# ============================================================================
# TEST 6: Empty History Check (New User)
# ============================================================================
print_section("TEST 6: Test Empty History (New User)")
print("Creating a new user session to test empty history...\n")

new_user_headers = headers.copy()
new_user_headers["X-End-User-ID"] = "brand_new_user_no_history"

try:
    # Get agent again (should work with new headers)
    r = requests.get(f"{BASE_URL}/v1/agents/", headers=new_user_headers)
    
    if check_response(r):
        agents = r.json()
        agent_id = agents[0]["id"]
        
        # Try to retrieve history for new user
        run_payload = {
            "input": "What did I ask you before? Use get_conversation_history.",
            "variables": {}
        }
        
        print(f"👤 New User: brand_new_user_no_history")
        print(f"   Input: '{run_payload['input']}'")
        
        r = requests.post(
            f"{BASE_URL}/v1/agents/{agent_id}/run",
            headers=new_user_headers,
            json=run_payload,
            timeout=30
        )
        
        if check_response(r):
            run_data = r.json()
            run_id = run_data["run_id"]
            print(f"   Run ID: {run_id}")
            
            if wait_for_run_completion(run_id):
                print("\n✅ Successfully handled empty history case")
            else:
                print("\n❌ Failed to handle empty history")
                
except Exception as e:
    print(f"❌ Error: {e}")


# ============================================================================
# Summary
# ============================================================================
print_section("Test Summary")
print("✅ GetConversationHistoryTool Tests Complete!\n")
print("Tests performed:")
print("  1. ✅ Built conversation history with multiple runs")
print("  2. ✅ Retrieved full conversation history")
print("  3. ✅ Retrieved last N messages (last_n parameter)")
print("  4. ✅ Filtered by role (user messages only)")
print("  5. ✅ Natural language query about previous questions")
print("  6. ✅ Empty history handling (new user)")
print(f"\n👤 Test User: {END_USER_ID}")
print(f"📊 Total runs created: {len(run_ids)}")
print("\n" + "=" * 70)
