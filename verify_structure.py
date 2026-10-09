#!/usr/bin/env python3
"""
Verification script to check that all key components can be imported.
This verifies the file structure matches the architecture specification.
"""

import sys
import os

# Add the current directory to the path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_import(module_name, description):
    """Test importing a module and report success/failure."""
    try:
        __import__(module_name)
        print(f"[OK] {description}")
        return True
    except ImportError as e:
        print(f"[FAIL] {description}: {e}")
        return False
    except Exception as e:
        print(f"[ERROR] {description}: {e}")
        return False

def main():
    print("Verifying AI Agent Gateway file structure...")
    print("=" * 50)
    
    all_passed = True
    
    # Core application modules
    all_passed &= test_import("app.main", "Main application")
    all_passed &= test_import("app.config", "Configuration")
    all_passed &= test_import("app.db", "Database setup")
    all_passed &= test_import("app.security", "Security utilities")
    
    # Models
    all_passed &= test_import("app.models.tables", "Database tables")
    all_passed &= test_import("app.models.agent_config", "Agent configuration")
    
    # Providers
    all_passed &= test_import("app.providers.base", "Provider base class")
    all_passed &= test_import("app.providers.openrouter", "OpenRouter provider")
    all_passed &= test_import("app.providers.nvidia", "NVIDIA provider")
    all_passed &= test_import("app.providers.registry", "Provider registry")
    
    # Engine components
    all_passed &= test_import("app.engine.types", "Engine types and schemas")
    all_passed &= test_import("app.engine.context", "Run context")
    all_passed &= test_import("app.engine.retry", "Retry mechanisms")
    all_passed &= test_import("app.engine.llm_utils", "LLM utilities")
    all_passed &= test_import("app.engine.prompts", "Prompt templates")
    all_passed &= test_import("app.engine.views", "Context views")
    all_passed &= test_import("app.engine.state_ops", "State operations")
    all_passed &= test_import("app.engine.tool_loop", "Tool loop")
    all_passed &= test_import("app.engine.nodes", "Graph nodes")
    all_passed &= test_import("app.engine.graph", "LangGraph construction")
    all_passed &= test_import("app.engine.runner", "Run execution")
    
    # Tools
    all_passed &= test_import("app.tools.base", "Tool base class")
    all_passed &= test_import("app.tools.registry", "Tool registry")
    all_passed &= test_import("app.tools.call", "Tool calling")
    all_passed &= test_import("app.tools.webhook", "Webhook tool")
    all_passed &= test_import("app.tools.client", "Client tool")
    all_passed &= test_import("app.tools.builtin", "Built-in tools")
    
    # Knowledge system
    all_passed &= test_import("app.knowledge.embedder", "Embedder")
    all_passed &= test_import("app.knowledge.retriever", "Knowledge retriever")
    all_passed &= test_import("app.knowledge.ingest", "Document ingestor")
    all_passed &= test_import("app.knowledge.factory", "Knowledge factory")
    
    # API modules
    all_passed &= test_import("app.api.auth", "Auth API")
    all_passed &= test_import("app.api.providers", "Providers API")
    all_passed &= test_import("app.api.agents", "Agents API")
    all_passed &= test_import("app.api.tools", "Tools API")
    all_passed &= test_import("app.api.knowledge", "Knowledge API")
    all_passed &= test_import("app.api.runs", "Runs API")
    
    print("=" * 50)
    if all_passed:
        print("[SUCCESS] All components imported successfully!")
        print("The file structure matches the architecture specification.")
        return 0
    else:
        print("[FAILURE] Some components failed to import.")
        print("This is expected if dependencies are not installed.")
        return 1

if __name__ == "__main__":
    sys.exit(main())