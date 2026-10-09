"""
Example: Updating Agent Prompts

Shows how to:
1. Get default prompts for reference
2. Create an agent with custom prompts
3. Update an existing agent's prompts
"""
import requests
import json

API_KEY = "gw_vfTUvJo08plY-Gmaz5PcR0x71HII7ad-b-gGDJTKIBs"
BASE_URL = "http://localhost:8000"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

print("=" * 80)
print("  EXAMPLE: Working with Custom Prompts")
print("=" * 80)

# ============================================================================
# STEP 1: Get Default Prompts for Reference
# ============================================================================
print("\n1️⃣  Getting default prompts...")
r = requests.get(f"{BASE_URL}/v1/agents/default-prompts")
if r.status_code == 200:
    data = r.json()
    print("✅ Default prompts retrieved")
    print(f"   Available prompt types: {list(data['default_prompts'].keys())}")
    print(f"   Built-in tools: {len(data['builtin_tools'])} tools")
    
    # Show one example
    print(f"\n   Example - Direct Prompt:")
    print(f"   {data['default_prompts']['direct']['prompt'][:100]}...")
else:
    print(f"❌ Failed: {r.status_code}")
    exit(1)

# ============================================================================
# STEP 2: Get Existing Agents
# ============================================================================
print("\n2️⃣  Checking existing agents...")
r = requests.get(f"{BASE_URL}/v1/agents/", headers=headers)
if r.status_code != 200:
    print(f"❌ Failed to get agents: {r.status_code}")
    exit(1)

agents = r.json()
print(f"✅ Found {len(agents)} agent(s)")

if not agents:
    print("\n⚠️  No agents found. Creating a sample agent first...")
    
    # Get configured providers
    r = requests.get(f"{BASE_URL}/v1/providers/configured", headers=headers)
    providers = r.json()
    if not providers:
        print("❌ No providers configured. Please configure a provider first.")
        exit(1)
    
    provider_id = providers[0]["id"]
    
    # Create agent without custom prompts (uses defaults)
    agent_config = {
        "name": "Sample Agent",
        "provider_id": provider_id,
        "models": {
            "planner": "meta-llama/llama-3.1-70b-instruct",
            "executor": "meta-llama/llama-3.1-70b-instruct",
            "reviewer": "meta-llama/llama-3.1-8b-instruct"
        },
        "system_prompt": "You are a helpful assistant.",
        "temperature": 0.7,
        "tools": [],
        "knowledge_base_ids": [],
        "limits": {
            "max_iterations": 5,
            "max_tokens_per_run": 50000,
            "timeout_s": 120
        }
    }
    
    r = requests.post(f"{BASE_URL}/v1/agents/", headers=headers, json=agent_config)
    if r.status_code != 200:
        print(f"❌ Failed to create agent: {r.status_code}")
        print(r.text)
        exit(1)
    
    agent_data = r.json()
    agent_id = agent_data["id"]
    print(f"✅ Created sample agent: {agent_id}")
else:
    agent_id = agents[0]["id"]
    print(f"✅ Using existing agent: {agent_id}")

# ============================================================================
# STEP 3: Update Agent with Custom Prompts
# ============================================================================
print(f"\n3️⃣  Updating agent {agent_id} with custom prompts...")

# Get current agent config
r = requests.get(f"{BASE_URL}/v1/agents/{agent_id}", headers=headers)
current_agent = r.json()

# Update with custom prompts
updated_config = current_agent["config"]

# Add custom direct prompt (friendly tone)
updated_config["direct_prompt"] = """Answer the user's question warmly and helpfully! 😊

Use these built-in tools when needed:
- get_conversation_history: Check what we discussed before
- store_kv/get_kv: Remember important info across conversations
- kb_search: Search the knowledge base

Be friendly, use emojis occasionally, and keep responses concise!"""

# Add custom planner prompt (detailed)
updated_config["planner_prompt"] = """You are a detailed planner. Break the user's goal into at most {max_steps} steps.

Guidelines:
- Make each step specific and actionable
- Use the built-in tools: read_artifact, store_kv, get_kv, kb_search
- Prefer fewer, well-defined steps over many vague ones
- Each step needs clear success criteria

Available tools will be provided in the context."""

# Add custom executor prompt
updated_config["executor_prompt"] = """Execute this single step carefully.

Built-in tools available:
- read_artifact(id): Read previous outputs
- list_artifacts(): See what's been created
- store_kv(key, value): Save data permanently
- get_kv(key): Retrieve saved data
- kb_search(query): Search knowledge base

Reply with JSON containing status, summary, output, kind, facts, and problems."""

# Update the agent
r = requests.put(f"{BASE_URL}/v1/agents/{agent_id}", headers=headers, json=updated_config)

if r.status_code == 200:
    print("✅ Agent updated successfully!")
    updated_agent = r.json()
    print(f"   Updated at: {updated_agent['updated_at']}")
    
    # Check which custom prompts are set
    config = updated_agent["config"]
    custom_prompts = []
    for prompt_type in ["router_prompt", "planner_prompt", "executor_prompt", 
                        "reviewer_prompt", "direct_prompt", "finalizer_prompt"]:
        if config.get(prompt_type):
            custom_prompts.append(prompt_type.replace("_prompt", ""))
    
    print(f"   Custom prompts: {', '.join(custom_prompts)}")
else:
    print(f"❌ Failed to update: {r.status_code}")
    print(r.text)

# ============================================================================
# STEP 4: Show Updated Configuration
# ============================================================================
print(f"\n4️⃣  Current agent configuration:")
r = requests.get(f"{BASE_URL}/v1/agents/{agent_id}", headers=headers)
agent = r.json()

print(f"   Name: {agent['config']['name']}")
print(f"   System Prompt: {agent['config']['system_prompt'][:50]}...")

if agent['config'].get('direct_prompt'):
    print(f"   ✅ Custom Direct Prompt (length: {len(agent['config']['direct_prompt'])} chars)")
else:
    print(f"   ⚪ Using default direct prompt")

if agent['config'].get('planner_prompt'):
    print(f"   ✅ Custom Planner Prompt (length: {len(agent['config']['planner_prompt'])} chars)")
else:
    print(f"   ⚪ Using default planner prompt")

if agent['config'].get('executor_prompt'):
    print(f"   ✅ Custom Executor Prompt (length: {len(agent['config']['executor_prompt'])} chars)")
else:
    print(f"   ⚪ Using default executor prompt")

# ============================================================================
# STEP 5: Test the Agent
# ============================================================================
print(f"\n5️⃣  Testing agent with custom prompts...")
test_payload = {
    "input": "Hello! Can you tell me a joke?",
    "variables": {}
}

r = requests.post(
    f"{BASE_URL}/v1/agents/{agent_id}/run",
    headers=headers,
    json=test_payload
)

if r.status_code == 200:
    run_data = r.json()
    print(f"✅ Run started: {run_data['run_id']}")
    print(f"   Check your server logs to see the custom prompts in action!")
    print(f"   Look for: 💬 [DIRECT] with your custom prompt")
else:
    print(f"❌ Failed to start run: {r.status_code}")

print("\n" + "=" * 80)
print("  SUMMARY")
print("=" * 80)
print("""
Key Points:
1. Get default prompts with: GET /v1/agents/default-prompts
2. Create agents with custom prompts in the config
3. Update agents with: PUT /v1/agents/{id}
4. All custom prompts are optional - defaults are used if not provided
5. Built-in tools are always available regardless of custom prompts

Custom prompts you can set:
- router_prompt: Routing decisions
- planner_prompt: Task decomposition
- executor_prompt: Step execution
- reviewer_prompt: Quality checking
- direct_prompt: Simple answers
- finalizer_prompt: Final answer composition
""")
