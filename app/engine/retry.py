import asyncio, hashlib, json, random
import httpx, openai

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}

def classify(exc: Exception) -> str:
    """'transient' = safe to retry the same call; anything else = do not blindly retry."""
    if isinstance(exc, (openai.RateLimitError, openai.APIConnectionError,
                        openai.APITimeoutError, openai.InternalServerError)):
        return "transient"
    if isinstance(exc, httpx.HTTPStatusError):
        return "transient" if exc.response.status_code in TRANSIENT_STATUS else "fatal"
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, asyncio.TimeoutError)):
        return "transient"
    return "fatal"

async def retry_async(fn, *, attempts: int = 3, base: float = 0.5, cap: float = 8.0, on_retry=None):
    """Await fn() with exponential backoff + jitter. Only transient errors are retried."""
    for n in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            if classify(exc) != "transient" or n == attempts - 1:
                raise
            delay = min(cap, base * (2 ** n)) * (0.5 + random.random() / 2)
            headers = getattr(getattr(exc, "response", None), "headers", None)
            retry_after = headers.get("retry-after") if headers else None
            if retry_after and str(retry_after).isdigit():
                delay = max(delay, min(float(retry_after), 30.0))
            if on_retry:
                await on_retry(n + 1, exc)
            await asyncio.sleep(delay)

def call_signature(tool: str, args: dict) -> str:
    """Stable id for 'same tool + same args'. Used for caching, idempotency, and loop detection."""
    raw = f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

def plan_hash(steps: list[dict]) -> str:
    raw = "|".join(sorted(s["goal"].strip().lower() for s in steps))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]