"""Hand a blocking challenge (e.g. a CAPTCHA) to a person at the visible browser, then continue.

The program never tries to solve the challenge itself: it waits, polling, until the person
has cleared it or the time runs out.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from playwright.async_api import Page

from portal_runner.core.errors import FinalError

log = logging.getLogger(__name__)

POLL_MS = 1000


async def wait_for_human(
    page: Page, still_blocked: Callable[[], Awaitable[bool]], what: str, timeout_s: float
) -> float:
    """Wait until ``still_blocked()`` is False; returns the seconds waited."""
    log.warning("%s: please solve it in the browser window (waiting up to %ds)", what, timeout_s)
    started = time.monotonic()
    while await still_blocked():
        if time.monotonic() - started > timeout_s:
            raise FinalError(f"{what} was not solved within {timeout_s:.0f}s")
        await page.wait_for_timeout(POLL_MS)
    waited = time.monotonic() - started
    log.info("%s: cleared after %.0fs, continuing", what, waited)
    return waited
