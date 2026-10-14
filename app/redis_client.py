"""
Redis client for pub/sub messaging.
Used for real-time streaming of agent execution events.
"""
import asyncio
import json
import redis.asyncio as redis
from typing import Optional
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

AGENT_PROMPT_CONFIG_TTL_SECONDS = 300
AGENT_EXECUTION_CONFIG_TTL_SECONDS = 480

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
    Append an event to a per-run Redis Stream.

    Args:
        channel: Redis Stream key (e.g., "run:abc-123")
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

        await client.xadd(channel, {"event": message}, maxlen=1000, approximate=True)
        # Keep enough history for reconnecting clients without retaining runs forever.
        await client.expire(channel, 86400)
        return True
    except Exception as e:
        logger.error(f"Failed to publish event to {channel}: {e}")
        return False


def _agent_prompt_config_key(user_id: str, agent_id: str) -> str:
    return f"agent:prompt-config:{user_id}:{agent_id}"


async def get_agent_prompt_config(user_id: str, agent_id: str) -> dict | None:
    """Read the short-lived, non-secret agent configuration cache."""
    try:
        client = await asyncio.wait_for(get_redis(), timeout=0.2)
        if not client:
            return None
        value = await asyncio.wait_for(
            client.get(_agent_prompt_config_key(user_id, agent_id)), timeout=0.2
        )
        if not value:
            return None
        cached = json.loads(value)
        return cached if isinstance(cached, dict) else None
    except Exception as exc:
        logger.debug("Could not read prompt config cache for agent %s: %s", agent_id, exc)
        return None


async def set_agent_prompt_config(user_id: str, agent_id: str, config: dict) -> None:
    """Cache an agent's non-secret prompt/config payload for 5 minutes."""
    try:
        client = await asyncio.wait_for(get_redis(), timeout=0.2)
        if client:
            await asyncio.wait_for(
                client.set(
                    _agent_prompt_config_key(user_id, agent_id),
                    json.dumps(config),
                    ex=AGENT_PROMPT_CONFIG_TTL_SECONDS,
                ),
                timeout=0.2,
            )
    except Exception as exc:
        logger.debug("Could not write prompt config cache for agent %s: %s", agent_id, exc)


async def invalidate_agent_prompt_config(user_id: str, agent_id: str) -> None:
    """Remove a cached prompt config after its agent is changed or deleted."""
    try:
        client = await asyncio.wait_for(get_redis(), timeout=0.2)
        if client:
            await asyncio.wait_for(
                client.delete(_agent_prompt_config_key(user_id, agent_id)), timeout=0.2
            )
    except Exception as exc:
        logger.debug("Could not invalidate prompt config cache for agent %s: %s", agent_id, exc)


def _agent_execution_config_key(user_id: str) -> str:
    # One per-user hash lets any provider/KB/tool mutation invalidate all agent
    # snapshots with one DEL, without scanning Redis keys.
    return f"agent:execution-config:{user_id}"


async def get_agent_execution_config(user_id: str, agent_id: str | None) -> dict | None:
    """Get the stable run configuration snapshot from Redis, if present."""
    if not agent_id:
        return None
    try:
        client = await asyncio.wait_for(get_redis(), timeout=0.2)
        if not client:
            return None
        value = await asyncio.wait_for(
            client.hget(_agent_execution_config_key(user_id), str(agent_id)), timeout=0.2
        )
        cached = json.loads(value) if value else None
        return cached if isinstance(cached, dict) else None
    except Exception as exc:
        logger.debug("Could not read run config cache for agent %s: %s", agent_id, exc)
        return None


async def set_agent_execution_config(
    user_id: str, agent_id: str | None, config: dict
) -> None:
    """Cache encrypted-secret config snapshots for 8 minutes."""
    if not agent_id:
        return
    try:
        client = await asyncio.wait_for(get_redis(), timeout=0.2)
        if client:
            key = _agent_execution_config_key(user_id)
            async with client.pipeline(transaction=False) as pipe:
                pipe.hset(key, str(agent_id), json.dumps(config))
                pipe.expire(key, AGENT_EXECUTION_CONFIG_TTL_SECONDS)
                await asyncio.wait_for(pipe.execute(), timeout=0.2)
    except Exception as exc:
        logger.debug("Could not write run config cache for agent %s: %s", agent_id, exc)


async def invalidate_agent_execution_configs(user_id: str) -> None:
    """Invalidate every execution snapshot for a user after config changes."""
    try:
        client = await asyncio.wait_for(get_redis(), timeout=0.2)
        if client:
            await asyncio.wait_for(client.delete(_agent_execution_config_key(user_id)), timeout=0.2)
    except Exception as exc:
        logger.debug("Could not invalidate run config cache for user %s: %s", user_id, exc)


