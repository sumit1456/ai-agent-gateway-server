#!/usr/bin/env python3
"""
Script to test LLM connectivity.
"""
import asyncio
import sys
from app.providers.registry import get_provider
from app.security import encrypt

async def main():
    if len(sys.argv) < 3:
        print("Usage: python ping_llm.py <provider_id> <api_key>")
        sys.exit(1)
    
    provider_id = sys.argv[1]
    api_key = sys.argv[2]
    
    try:
        provider = get_provider(provider_id)
        is_valid = await provider.validate_api_key(api_key)
        if is_valid:
            print(f"✓ {provider.display_name} API key is valid")
        else:
            print(f"✗ {provider.display_name} API key is invalid")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())