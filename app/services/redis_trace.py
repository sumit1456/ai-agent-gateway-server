"""Dedicated, payload-safe diagnostics for Redis client operations."""

import os
import threading
import time
from datetime import datetime
from app.services.log_paths import get_log_dir


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


REDIS_TRACE_ENABLED = _env_bool("REDIS_TRACE_ENABLED", True)
_write_lock = threading.Lock()


def log_redis_operation(operation: str, duration_ms: float, status: str = "ok", detail: str = "") -> None:
    """Append one Redis operation measurement without logging payloads or secrets."""
    if not REDIS_TRACE_ENABLED:
        return

    try:
        log_dir = get_log_dir()
        path = log_dir / f"redis_trace_{datetime.now():%Y%m%d}.log"
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        suffix = f" | {detail}" if detail else ""
        line = f"{timestamp} | {operation} | {duration_ms:.1f}ms | {status}{suffix}\n"
        with _write_lock:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except OSError:
        # Diagnostics must never interrupt Redis operations or agent runs.
        return


async def measure_redis(operation: str, awaitable_factory, detail: str = ""):
    """Measure an awaited Redis operation and log its result."""
    started = time.perf_counter()
    status = "ok"
    try:
        return await awaitable_factory()
    except Exception as exc:
        status = f"error:{type(exc).__name__}"
        raise
    finally:
        log_redis_operation(operation, (time.perf_counter() - started) * 1000, status, detail)
