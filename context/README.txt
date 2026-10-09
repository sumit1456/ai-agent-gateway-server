================================================================================
AI AGENT GATEWAY - CONTEXT DIRECTORY
================================================================================

This directory provides rapid context for LLMs and developers working on this
codebase. It covers the core architecture, backend API endpoints, database models,
frontend integration, and ongoing bug investigations.

SUMMARY OF FILES IN THIS DIRECTORY:
--------------------------------------------------------------------------------
1. app_api_agents.txt
   - Deep dive into `app/api/agents.py`.
   - CRUD endpoints for agents, provider resolution, and execution handling.

2. app_models_tables.txt
   - Deep dive into `app/models/tables.py`.
   - SQLModel database schemas (User, Provider, Agent, AgentSession, Run, Tool, KB).
   - Foreign key relationships and cascade behaviors.

3. app_api_providers.txt
   - Deep dive into `app/api/providers.py`.
   - Provider registration, model fetching, deletion, and encryption.

4. app_api_tools.txt
   - Deep dive into `app/api/tools.py`.
   - Tool endpoints, schema validation, and storage.

5. app_api_knowledge.txt
   - Deep dive into `app/api/knowledge.py`.
   - Knowledge base creation, document ingestion, Pinecone vector search, and deletion.

6. frontend_api.txt
   - Deep dive into `frontend/src/api.js`.
   - Client-side fetch helpers for auth, providers, agents, tools, knowledge bases.

7. frontend_dashboard.txt
   - Deep dive into `frontend/src/pages/Dashboard.jsx`.
   - UI views, tabs, modal actions, and how agent / provider deletions are triggered.

8. delete_agent_500_investigation.txt
   - Focused post-mortem on why `DELETE /v1/agents/{agent_id}` returned HTTP 500
     while `DELETE /v1/providers/{provider_id}` succeeded.
   - Root cause analysis and applied cascade deletion fix.
