"""
Provider Configuration Checker

This script helps diagnose provider configuration issues.
"""
import requests
import json
import sys

# Configuration
API_KEY = "gw_vfTUvJo08plY-Gmaz5PcR0x71HII7ad-b-gGDJTKIBs"
BASE_URL = "http://localhost:8000"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}


def print_section(title):
    """Print a section header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


print_section("Provider Configuration Diagnostic Tool")

# Step 1: Check configured providers
print("Checking configured providers...\n")
try:
    r = requests.get(f"{BASE_URL}/v1/providers/configured", headers=headers)
    if r.status_code != 200:
        print(f"❌ Failed to fetch providers: {r.status_code}")
        print(r.text)
        sys.exit(1)
    
    providers = r.json()
    
    if not providers:
        print("❌ No providers configured!")
        print("\nYou need to add a provider first:")
        print("""
POST /v1/providers/
{
    "provider_id": "nvidia",  // or "openai", "anthropic", etc.
    "api_key": "your-api-key-here"
}
        """)
        sys.exit(1)
    
    print(f"✅ Found {len(providers)} configured provider(s)\n")
    
    for i, provider in enumerate(providers, 1):
        print(f"{i}. Provider: {provider['provider_id']}")
        print(f"   ID: {provider['id']}")
        print(f"   Default Model: {provider.get('default_model', 'N/A')}")
        print(f"   Enabled Models: {len(provider.get('enabled_models', []))} models")
        print(f"   Created: {provider.get('created_at', 'N/A')}")
        print()
        
        # Try to fetch models to validate API key
        print(f"   Testing API key by fetching models...")
        try:
            model_r = requests.get(
                f"{BASE_URL}/v1/providers/{provider['provider_id']}/models",
                headers=headers,
                timeout=10
            )
            
            if model_r.status_code == 200:
                models = model_r.json().get('models', [])
                print(f"   ✅ API key is valid! ({len(models)} models available)")
            elif model_r.status_code == 502:
                print(f"   ❌ API key validation failed!")
                print(f"   Error: {model_r.json().get('detail', 'Unknown error')}")
                print(f"\n   → The API key for {provider['provider_id']} appears to be invalid.")
                print(f"   → Please update it with a valid key:")
                print(f"\n   PUT /v1/providers/{provider['provider_id']}")
                print(f'   {{ "api_key": "your-valid-key-here" }}\n')
            else:
                print(f"   ⚠️  Unexpected response: {model_r.status_code}")
                
        except requests.Timeout:
            print(f"   ⏱️  Timeout - provider may be slow or unavailable")
        except Exception as e:
            print(f"   ❌ Error testing API key: {e}")
        
        print()

except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)


# Step 2: Check agents
print_section("Checking Agents")
try:
    r = requests.get(f"{BASE_URL}/v1/agents/", headers=headers)
    if r.status_code != 200:
        print(f"❌ Failed to fetch agents: {r.status_code}")
        print(r.text)
    else:
        agents = r.json()
        print(f"Found {len(agents)} agent(s)\n")
        
        for i, agent in enumerate(agents, 1):
            print(f"{i}. Agent: {agent['name']}")
            print(f"   ID: {agent['id']}")
            print(f"   Provider ID: {agent['provider_id']}")
            
            # Check if provider still exists
            provider_exists = any(p['id'] == agent['provider_id'] for p in providers)
            if provider_exists:
                provider = next(p for p in providers if p['id'] == agent['provider_id'])
                print(f"   Provider: {provider['provider_id']} ✅")
            else:
                print(f"   Provider: NOT FOUND ❌")
                print(f"   → This agent references a deleted provider!")
            print()

except Exception as e:
    print(f"❌ Error checking agents: {e}")


print_section("Summary")
print("Configuration check complete!")
print("\nNext steps:")
print("1. Ensure all providers have valid API keys")
print("2. Test with: python test_agent_run.py")
