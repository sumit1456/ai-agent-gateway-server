"""
Main FastAPI application entry point.
"""
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import time
from app.api import auth, providers, agents, tools, knowledge, runs
from app.config import settings
from app.db import init_db
from app.logging_config import setup_logging, get_logger

# Configure logging with UTF-8 encoding for Windows compatibility
setup_logging()
logger = get_logger(__name__)

app = FastAPI(title="AI Agent Gateway", version="0.1.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080"],  # Frontend URLs
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
app.include_router(tools.router, prefix="/v1/tools", tags=["tools"])
app.include_router(knowledge.router, prefix="/v1/knowledge-bases", tags=["knowledge"])
app.include_router(runs.router, prefix="/v1", tags=["runs"])

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    await init_db()
    logger.info("AI Agent Gateway started successfully")

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