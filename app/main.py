"""
Main FastAPI application entry point.
"""
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import time
from app.api import auth, providers, agents, tools, knowledge, runs, workflows, webhooks
from app.config import settings
from app.db import init_db
from app.logging_config import setup_logging, get_logger
from app.redis_client import close_redis, get_redis

# Configure logging with UTF-8 encoding for Windows compatibility
setup_logging()
logger = get_logger(__name__)

app = FastAPI(title="AI Agent Gateway", version="0.1.0")
_webhook_worker_task: asyncio.Task | None = None

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080", "https://agent-gateway.corstack.in" , "https://agent-gateway.corstack.in"],  # Frontend URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests."""
    start_time = time.time()
    
    # Log request
    logger.info(f"Request: {request.method} {request.url.path}")
    
    # Process request
    response = await call_next(request)
    
    # Log response
    duration = (time.time() - start_time) * 1000
    logger.info(
        f"Response: {request.method} {request.url.path} "
        f"status={response.status_code} duration={duration:.2f}ms"
    )
    
    return response

# Include API routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(providers.router, prefix="/v1/providers", tags=["providers"])
app.include_router(agents.router, prefix="/v1/agents", tags=["agents"])
app.include_router(workflows.router, prefix="/v1/workflows", tags=["workflows"])
app.include_router(tools.router, prefix="/v1/tools", tags=["tools"])
app.include_router(knowledge.router, prefix="/v1/knowledge-bases", tags=["knowledge"])
app.include_router(runs.router, prefix="/v1", tags=["runs"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["webhooks"])

@app.on_event("startup")
async def startup_event():
    """Initialize and verify required and optional service connections."""
    logger.info("Checking PostgreSQL connection and initializing schema...")
    try:
        # init_db opens a real database connection and applies the schema setup.
        await init_db()
    except Exception:
        logger.exception("PostgreSQL startup check failed; application startup aborted")
        raise
    logger.info("PostgreSQL connection successful; database schema is ready")

    global _webhook_worker_task
    from app.webhooks import delivery_worker
    _webhook_worker_task = asyncio.create_task(delivery_worker(), name="webhook-delivery-worker")
    logger.info("Webhook delivery worker started")

    if not settings.redis_url:
        logger.info("Redis is not configured (REDIS_URL is empty); live SSE streaming is disabled")
    else:
        logger.info("Checking Redis connection...")
        redis_client = await get_redis()
        if redis_client:
            logger.info("Redis startup check successful; live SSE streaming is enabled")
        else:
            # Redis is optional: the API can still serve requests and clients can poll runs.
            logger.warning("Redis startup check failed; continuing without live SSE streaming")

    logger.info("AI Agent Gateway started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Close shared Redis resources on shutdown."""
    if _webhook_worker_task:
        _webhook_worker_task.cancel()
        try:
            await _webhook_worker_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    logger.info("AI Agent Gateway shutdown complete")

@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "AI Agent Gateway",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "ai-agent-gateway"
    }
