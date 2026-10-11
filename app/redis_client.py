"""
Redis client for pub/sub messaging.
Used for real-time streaming of agent execution events.
"""
import redis.asyncio as redis
from typing import Optional
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_redis_client: Optional[redis.Redis] = None


async def get_redis() -> Optional[redis.Redis]:
    """
    Get or create Redis client.
    Returns None if Redis is not configured.
    """
    global _redis_client

    if not settings.redis_url:
        return None

    if _redis_client is None:
        try:
            redis_url = settings.redis_url
            if settings.redis_ssl and redis_url.startswith("redis://"):
                redis_url = "rediss://" + redis_url[len("redis://"):]
            _redis_client = redis.from_url(
                redis_url,
                password=settings.redis_password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30
            )
            # Test connection
            await _redis_client.ping()
            logger.info("Redis connection established")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. SSE streaming will be unavailable.")
            if _redis_client is not None:
                try:
                    await _redis_client.aclose()
                except Exception:
                    pass
            _redis_client = None

    return _redis_client


async def close_redis():
    """Close Redis connection on shutdown."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")


async def publish_event(channel: str, event_type: str, data: dict) -> bool:
    """
    Publish an event to a Redis channel.

    Args:
        channel: Redis channel name (e.g., "run:abc-123")
        event_type: Event type (e.g., "step_start", "tool_call", "done")
        data: Event data dictionary

    Returns:
        True if published successfully, False if Redis unavailable
    """
    client = await get_redis()
    if not client:
        return False

    try:
        import json
        from datetime import datetime, timezone

        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        await client.publish(channel, message)
        return True
    except Exception as e:
        logger.error(f"Failed to publish event to {channel}: {e}")
        return False
