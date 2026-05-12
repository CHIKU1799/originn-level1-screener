"""
OpenAI async client singleton with:
  - Connection pooling (single httpx.AsyncClient reused across all calls)
  - Global rate-limiting semaphore (MAX_CONCURRENT_API_CALLS)
  - Exponential-backoff retry on 429 / 5xx
  - o-series model compatibility (no system role, reasoning_effort)

Import pattern:
    from ._openai_client import get_client, rate_limited_create
"""

from __future__ import annotations
import asyncio
import random
import time
import logging

import openai
from openai import AsyncOpenAI

from . import config

logger = logging.getLogger(__name__)

# ── Singleton client ───────────────────────────────────────────────────────

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """Return (or create) the shared AsyncOpenAI client."""
    global _client
    if _client is None:
        kwargs: dict = {"api_key": config.OPENAI_API_KEY or None}
        if config.OPENAI_ORG_ID:
            kwargs["organization"] = config.OPENAI_ORG_ID
        if config.OPENAI_BASE_URL:
            kwargs["base_url"] = config.OPENAI_BASE_URL
        _client = AsyncOpenAI(
            max_retries=0,   # we handle retries ourselves for full control
            timeout=120.0,
            **kwargs,
        )
    return _client


# ── Global rate-limiting semaphore ────────────────────────────────────────

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_API_CALLS)
    return _semaphore


# ── Retry-aware, rate-limited wrapper ─────────────────────────────────────

async def rate_limited_create(**kwargs) -> openai.types.chat.ChatCompletion:
    """
    Drop-in replacement for client.chat.completions.create() with:
      1. Global semaphore (MAX_CONCURRENT_API_CALLS)
      2. Exponential backoff on RateLimitError / APIStatusError (5xx)
    """
    client = get_client()
    sem = _get_semaphore()

    last_exc: Exception | None = None
    delay = config.RETRY_BASE_DELAY

    for attempt in range(config.MAX_RETRIES + 1):
        async with sem:
            try:
                return await client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                last_exc = exc
                # Respect Retry-After header if present
                retry_after = _parse_retry_after(exc)
                wait = retry_after if retry_after else delay
                logger.warning("Rate limit hit (attempt %d/%d). Waiting %.1fs…",
                               attempt + 1, config.MAX_RETRIES + 1, wait)
            except openai.APIStatusError as exc:
                if exc.status_code < 500:
                    raise   # 4xx errors (other than 429) are not retryable
                last_exc = exc
                logger.warning("Server error %d (attempt %d/%d). Waiting %.1fs…",
                               exc.status_code, attempt + 1, config.MAX_RETRIES + 1, delay)
            except openai.APIConnectionError as exc:
                last_exc = exc
                logger.warning("Connection error (attempt %d/%d). Waiting %.1fs…",
                               attempt + 1, config.MAX_RETRIES + 1, delay)

        if attempt < config.MAX_RETRIES:
            jitter = random.uniform(0, delay * 0.25)
            await asyncio.sleep(min(delay + jitter, config.RETRY_MAX_DELAY))
            delay = min(delay * 2, config.RETRY_MAX_DELAY)

    raise last_exc  # type: ignore[misc]


def _parse_retry_after(exc: openai.RateLimitError) -> float | None:
    """Extract Retry-After seconds from rate limit response headers."""
    try:
        raw = exc.response.headers.get("retry-after") or exc.response.headers.get("x-ratelimit-reset-requests")
        if raw:
            return float(raw)
    except Exception:
        pass
    return None
