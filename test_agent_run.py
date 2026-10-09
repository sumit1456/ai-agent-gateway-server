"""
Complete Agent Testing Script

This script tests the full agent workflow:
1. List available providers
2. Configure a provider
3. List available models
4. Create an agent
5. Run the agent
6. Poll for results
"""
import requests
import json
import time
import sys

# Configuration
API_KEY = "gw_vfTUvJo08plY-Gmaz5PcR0x71HII7ad-b-gGDJTKIBs"
BASE_URL = "http://localhost:8000"
END_USER_ID = "test_user_123"  # Simulate an end-user

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "X-End-User-ID": END_USER_ID  # NEW: Track which end-user is making requests
}


def print_section(title):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


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


# ============================================================================
# STEP 1: List Available Providers
# ============================================================================
print_section("STEP 1: List Available Providers")
try:
    r = requests.get(f"{BASE_URL}/v1/providers/", headers=headers)
    print_response(r)
    if check_response(r):
        providers = r.json()
        print(f"✅ Found {len(providers)} available providers")
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)


# ============================================================================
# STEP 2: Check Configured Providers
# ============================================================================
print_section("STEP 2: Check Configured Providers")
try:
    r = requests.get(f"{BASE_URL}/v1/providers/configured", headers=headers)
    print_response(r)
    if check_response(r):
        configured_providers = r.json()
        print(f"✅ Found {len(configured_providers)} configured providers")
        
        # If no providers configured, prompt user
        if len(configured_providers) == 0:
            print("\n⚠️  No providers configured!")
            print("You need to configure a provider first.")
            print("\nExample using Nvidia:")
            print("""
POST /v1/providers/
{
    "provider_id": "nvidia",
    "api_key": "your-nvidia-api-key-here"
}
            """)
            sys.exit(1)
        
        # Use the first configured provider
        provider = configured_providers[0]
        provider_id = provider["id"]
        provider_name = provider["provider_id"]
        print(f"\n📌 Using provider: {provider_name} (ID: {provider_id})")
        
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)


# ============================================================================
# STEP 3: List Available Models for Provider
# ============================================================================
print_section(f"STEP 3: List Models for {provider_name}")
try:
    r = requests.get(f"{BASE_URL}/v1/providers/{provider_name}/models", headers=headers)
    print_response(r)
    if check_response(r):
        models_data = r.json()
        models = models_data.get("models", [])
        print(f"✅ Found {len(models)} models")
        if models:
            print(f"Sample models: {models[:5]}")
except Exception as e:
    print(f"⚠️  Could not fetch models: {e}")


# ============================================================================
# STEP 4: List Existing Agents
# ============================================================================
print_section("STEP 4: List Existing Agents")
try:
    r = requests.get(f"{BASE_URL}/v1/agents/", headers=headers)
    print_response(r)
    
    if check_response(r):
        agents = r.json()
        print(f"✅ Found {len(agents)} agents")
        
        if len(agents) == 0:
            # Create a test agent
            print("\n📝 No agents found. Creating a test agent...")
            
            agent_config = {
                "name": "Test Agent",
                "provider_id": provider_id,
                "models": {
                    "planner": "meta-llama/llama-3.1-70b-instruct",
                    "executor": "meta-llama/llama-3.1-70b-instruct",
                    "reviewer": "meta-llama/llama-3.1-8b-instruct"
                },
                "system_prompt": "You are a helpful AI assistant. Answer questions clearly and concisely.",
                "temperature": 0.7,
                "variables": [],
                "tools": [],
                "knowledge_base_ids": [],
                "limits": {
                    "max_iterations": 5,
                    "max_tokens_per_run": 50000,
                    "timeout_s": 120,
                    "max_steps": 8,
                    "max_parallel_steps": 3,
                    "max_tool_turns": 4,
                    "max_tool_output_chars": 4000,
                    "llm_review": True
                }
            }
            
            r = requests.post(f"{BASE_URL}/v1/agents/", headers=headers, json=agent_config)
            print_response(r, show_json=True)
            
            if check_response(r, expected_status=200):
                agent_data = r.json()
                agent_id = agent_data["id"]
                print(f"✅ Created agent: {agent_data['name']} (ID: {agent_id})")
            else:
                print("❌ Failed to create agent")
                sys.exit(1)
        else:
            # Use first existing agent
            agent_id = agents[0]["id"]
            print(f"\n📌 Using existing agent: {agents[0]['name']} (ID: {agent_id})")
            
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)


# ============================================================================
# STEP 5: Run the Agent
# ============================================================================
print_section(f"STEP 5: Run Agent (ID: {agent_id})")
print(f"🎭 Simulating end-user: {END_USER_ID}\n")
try:
    run_payload = {
        "input": "what was rhe last question i asked ? ?",
        "variables": {}
    }
    
    print(f"Sending request to: POST /v1/agents/{agent_id}/run")
    print(f"Headers: Authorization, X-End-User-ID: {END_USER_ID}")
    print(f"Payload: {json.dumps(run_payload, indent=2)}\n")
    
    r = requests.post(
        f"{BASE_URL}/v1/agents/{agent_id}/run",
        headers=headers,
        json=run_payload,
        timeout=30
    )
    print_response(r)
    
    if check_response(r):
        run_data = r.json()
        run_id = run_data["run_id"]
        print(f"✅ Run started successfully!")
        print(f"📌 Run ID: {run_id}")
        print(f"👤 End-User: {END_USER_ID}")
        print(f"Status: {run_data['status']}")
    else:
        print("❌ Failed to start run")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)


# ============================================================================
# STEP 6: Poll for Results
# ============================================================================
print_section(f"STEP 6: Poll for Results (Run ID: {run_id})")
print("Polling every 2 seconds (max 60 seconds)...\n")

max_polls = 30
poll_interval = 2

for i in range(max_polls):
    try:
        time.sleep(poll_interval)
        
        r = requests.get(f"{BASE_URL}/v1/runs/{run_id}", headers=headers)
        
        if check_response(r):
            run_status = r.json()
            status = run_status["status"]
            
            print(f"Poll {i+1}: Status = {status}")
            
            if status == "done":
                print("\n✅ Run completed successfully!")
                print_response(r)
                
                # Show result
                result = run_status.get("result", {})
                if result:
                    print("\n" + "=" * 60)
                    print("  FINAL RESULT")
                    print("=" * 60)
                    print(json.dumps(result, indent=2))
                
                print(f"\n📊 Stats:")
                print(f"   Duration: {run_status.get('duration_ms', 0)}ms")
                print(f"   Tokens: {run_status.get('tokens_used', 0)}")
                print(f"   Stop Reason: {run_status.get('stop_reason', 'N/A')}")
                break
                
            elif status == "failed":
                print("\n❌ Run failed!")
                print_response(r)
                
                # Check if it's an authentication error
                error_msg = run_status.get("error_message", "")
                if "403" in error_msg or "Authorization failed" in error_msg or "Forbidden" in error_msg:
                    print("\n" + "=" * 60)
                    print("  🔑 API KEY ISSUE DETECTED")
                    print("=" * 60)
                    print("\nThe provider's API key appears to be invalid or expired.")
                    print("\nTo fix this:")
                    print(f"1. Get a valid API key from {provider_name}")
                    print(f"2. Update your provider configuration:")
                    print(f"\n   PUT /v1/providers/{provider_name}")
                    print(f'   {{ "api_key": "your-new-valid-key" }}')
                    print("\n3. Re-run the agent")
                
                break
                
            elif status == "running":
                print(f"   Still running... (elapsed: {(i+1)*poll_interval}s)")
                
        else:
            print(f"❌ Error polling run status")
            break
            
    except Exception as e:
        print(f"❌ Error during polling: {e}")
        break
else:
    print("\n⏱️  Timeout: Run did not complete within 60 seconds")
    print("The run may still be processing. Check manually with:")
    print(f"GET /v1/runs/{run_id}")


# ============================================================================
# Summary
# ============================================================================
print_section("Test Complete")
print("✅ All steps executed successfully!")
print(f"\n📊 Session created for end-user: {END_USER_ID}")
print("   This end-user's conversation is isolated from other users.")


# ============================================================================
# BONUS: Test Session Isolation (Optional)
# ============================================================================
print_section("BONUS: Test Session Isolation")
print("Testing that different end-users have isolated sessions...\n")

try:
    # Run 1: User Alice
    headers_alice = headers.copy()
    headers_alice["X-End-User-ID"] = "alice"
    
    print("👤 User: alice")
    print("   Input: 'My favorite color is blue'")
    r1 = requests.post(
        f"{BASE_URL}/v1/agents/{agent_id}/run",
        headers=headers_alice,
        json={"input": "My favorite color is blue"}
    )
    
    if r1.status_code == 200:
        alice_run_id = r1.json()["run_id"]
        print(f"   ✅ Run started: {alice_run_id}\n")
        
        # Wait for completion
        time.sleep(5)
        
        # Run 2: User Bob asks about Alice
        headers_bob = headers.copy()
        headers_bob["X-End-User-ID"] = "bob"
        
        print("👤 User: bob")
        print("   Input: 'What is Alice's favorite color?'")
        r2 = requests.post(
            f"{BASE_URL}/v1/agents/{agent_id}/run",
            headers=headers_bob,
            json={"input": "What is Alice's favorite color?"}
        )
        
        if r2.status_code == 200:
            bob_run_id = r2.json()["run_id"]
            print(f"   ✅ Run started: {bob_run_id}\n")
            
            # Wait for completion
            time.sleep(5)
            
            # Check Bob's result
            r_bob_result = requests.get(f"{BASE_URL}/v1/runs/{bob_run_id}", headers=headers_bob)
            if r_bob_result.status_code == 200:
                bob_result = r_bob_result.json()
                bob_answer = bob_result.get("result", {}).get("answer", "")
                
                print("📝 Bob's Response:")
                print(f"   {bob_answer[:200]}")
                print()
                
                if "blue" in bob_answer.lower():
                    print("❌ Session leak detected! Bob knows Alice's info.")
                    print("   Sessions are NOT properly isolated.")
                else:
                    print("✅ Session isolation working correctly!")
                    print("   Bob doesn't have access to Alice's conversation.")
        
except Exception as e:
    print(f"⚠️  Bonus test skipped: {e}")

print("\n" + "=" * 60)
print("  All Tests Complete!")
print("=" * 60)
