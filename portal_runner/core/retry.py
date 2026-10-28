"""Bounded retries with exponential backoff, for retryable errors only."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from portal_runner.core.errors import RetryableError
from portal_runner.core.resolver import ResolveError

log = logging.getLogger(__name__)
T = TypeVar("T")

TRANSIENT_NETWORK = ("net::ERR_", "page crashed", "Target closed")
BACKOFF_CAP_S = 8.0


def is_retryable(e: BaseException) -> bool:
    """Not-ready-yet failures retry. Final errors and unexpected bugs do not."""
    if isinstance(e, RetryableError | ResolveError | PlaywrightTimeout):
        return True
    return isinstance(e, PlaywrightError) and any(m in str(e) for m in TRANSIENT_NETWORK)


def backoff_delay(attempt: int, jitter: float = 0.25) -> float:
    """1 s, 2 s, 4 s, ... capped, with jitter."""
    return min(BACKOFF_CAP_S, 2.0 ** (attempt - 1)) * (1 + random.uniform(-jitter, jitter))  # noqa: S311


async def with_retries(
    fn: Callable[[int], Awaitable[T]],
    *,
    what: str,
    attempts: int = 3,
    reset: Callable[[], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Call ``fn(attempt)``; on a retryable error wait, ``reset`` and try again."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn(attempt)
        except Exception as e:
            if not is_retryable(e) or attempt == attempts:
                if is_retryable(e):
                    log.error("%s: giving up after %d attempts (%s)", what, attempt, type(e).__name__)
                raise
            delay = backoff_delay(attempt)
            log.warning(
                "%s: attempt %d/%d failed (%s), retrying in %.1fs", what, attempt, attempts, type(e).__name__, delay
            )
            await sleep(delay)
            if reset:
                await reset()
    raise ValueError("attempts must be >= 1")
