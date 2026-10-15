# AI Agent Gateway

A implementation of the AI Agent Gateway architecture as specified in [ARCHITECTURE_FULL.md](ARCHITECTURE_FULL.md).

## Overview

This project implements a gateway service that allows developers to register, configure AI agents once, and call them via a single endpoint. The gateway handles all internal agent operations including routing, planning, execution, review, retries, context management, and RAG (Retrieval-Augmented Generation).

## Features

- **Agent Registration & Configuration**: Register agents with custom LLMs, tools, and knowledge bases
- **Three-Tier Tool Execution**: 
  - Tier 1: Fast async I/O (web search, HTTP requests)
  - Tier 2: Heavy compute (OCR, code execution, document parsing)
  - Tier 3: External/MCP (client webhooks, MCP servers)
- **RAG Layer**: Per-agent knowledge bases using Pinecone vector storage
- **Memory Management**: Multiple memory strategies (sliding window, buffer summary, RAG episodic)
- **Execution Engine**: LangGraph-based Plan→Execute→Review loop with retry policies
- **Flexible LLM Provider Support**: OpenRouter and NVIDIA NIM implementations with easy extension
- **Secure Credential Management**: API key encryption using Fernet (AES-128)
- **RESTful API**: Full API for managing agents, providers, tools, knowledge bases, and runs
- **Real-time Responses**: Server-Sent Events (SSE) for streaming agent responses

### Run progress streaming

Configure Redis to enable authenticated SSE progress events and final run results. The same bearer token used to create a run authenticates the stream request. Run status polling remains available as a fallback.

```env
REDIS_URL=redis://localhost:6379
REDIS_SSL=false
REDIS_PASSWORD=
```

For TLS Redis services, set `REDIS_URL` to an `rediss://` URL and `REDIS_SSL=true`. Subscribe to `GET /v1/runs/{run_id}/stream` with the same bearer token used for other run endpoints. The stream emits a sanitized `progress_snapshot`, plan/task lifecycle events, `answer.delta` text chunks, `answer.reset` when an in-progress draft must be discarded (for example, after a tool call), and terminal `run_completed` or `run_failed` events. It does not stream private model reasoning, prompts, or raw tool output. Without reachable Redis, this endpoint returns `503`; use `GET /v1/runs/{run_id}` for polling.

### Read run results with SSE

After creating a run, subscribe to GET /v1/runs/{run_id}/stream with the same Authorization bearer token. The stream replays Redis events, sends progress, and ends with run_completed or run_failed carrying the terminal result. No webhook endpoint or separate signing token is required. Keep the gateway token on your backend and relay events to your frontend over your own authenticated connection. Redis must be reachable for SSE; GET /v1/runs/{run_id} remains available for polling.

## Architecture

The implementation follows the exact file structure and component organization specified in the architecture document:

```
ai-agent-gateway/
├── app/                    # Main application code
│   ├── api/               # API endpoints
│   ├── engine/            # Agent execution engine (LangGraph)
│   ├── knowledge/         # RAG/Knowledge base system
│   ├── models/            # Database models
│   ├── providers/         # LLM provider implementations
│   ├── security.py        # Security utilities
│   ├── main.py            # Application entry point
│   ├── config.py          # Configuration management
│   └── db.py              # Database setup
├── tests/                 # Test suite
├── scripts/               # Utility scripts
├── golden/                # Golden test data
├── requirements.txt       # Python dependencies
├── docker-compose.yml     # Docker Compose setup
├── .env.example           # Environment template
└── ARCHITECTURE_FULL.md   # Original architecture specification
```

## Installation

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Set up environment variables using `.env.example` as a template
4. Start PostgreSQL: `docker-compose up -d`
5. Initialize database: The application will create tables on first startup
6. Run the application: `python scripts/run_local.py`

## API Documentation

Once the application is running, visit `http://localhost:8000/docs` for interactive API documentation.

## Implementation Status

This implementation provides the complete file structure and core components as specified in the architecture document. All major components are in place:

- ✓ Database models (User, Agent, Provider, Tool, KnowledgeBase, Run, Artifact)
- ✓ Agent configuration system (AgentConfig Pydantic model)
- ✓ LLM provider interface and implementations (OpenRouter, NVIDIA NIM)
- ✓ Tool system with webhook and client tool support
- ✓ Knowledge/RAG system with embedding and retrieval capabilities
- ✓ LangGraph-based agent execution engine (Plan→Execute→Review loop)
- ✓ API endpoints for all entities
- ✓ Security utilities (encryption, authentication)
- ✓ Configuration management

## Next Steps

To complete the implementation, the following would need to be addressed:

1. Actual integration with Pinecone for vector storage
2. Implementation of specific built-in tools (web search, code execution, etc.)
3. Authentication flow refinement (JWT/OAuth2)
4. Comprehensive test suite
5. Production hardening (SSRF protection, rate limiting, etc.)
6. Documentation and examples

However, the core architectural framework is fully implemented and ready for extension.
