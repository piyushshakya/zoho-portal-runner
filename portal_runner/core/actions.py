"""Clicks and waits that explain themselves when they fail."""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from portal_runner.core.errors import RetryableError
from portal_runner.core.popups import PopupGuard

log = logging.getLogger(__name__)

CLICK_TIMEOUT_MS = 8000
SETTLE_TIMEOUT_MS = 2500
POLL_MS = 250
DIAGNOSE_TIMEOUT_MS = 2000

_COVER_JS = """
(el) => {
  const r = el.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (!top) return 'nothing (element is off-screen)';
  if (top === el || el.contains(top)) return null;
  const cls = (top.getAttribute('class') || '').split(/\\s+/).slice(0, 4).join('.');
  const text = (top.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  return `<${top.tagName.toLowerCase()}${cls ? ' class=' + cls : ''}> "${text}"`;
}
"""


async def covering_element(locator: Locator) -> str | None:
    """Describe what is painted over the element's centre, or None."""
    try:
        return await locator.evaluate(_COVER_JS, timeout=DIAGNOSE_TIMEOUT_MS)
    except Exception as e:
        return f"unknown ({type(e).__name__})"


async def click(
    page: Page,
    locator: Locator,
    guard: PopupGuard,
    what: str,
    *,
    timeout_ms: int = CLICK_TIMEOUT_MS,
    keyboard_fallback: bool = False,
) -> None:
    """Click; if blocked: log what covers it, optionally press Enter on it (links/buttons),
    clear popups and retry once, then fail naming the blocking element."""
    try:
        await locator.click(timeout=timeout_ms)
        return
    except PlaywrightTimeout:
        log.warning("click on %s blocked; on top: %s", what, await covering_element(locator))
    if keyboard_fallback:
        try:
            await locator.focus(timeout=timeout_ms)
            await page.keyboard.press("Enter")
            log.info("activated %s with the keyboard instead", what)
            return
        except PlaywrightTimeout:
            pass
    await guard.dismiss_any(page)
    await page.keyboard.press("Escape")
    try:
        await locator.click(timeout=timeout_ms)
    except PlaywrightTimeout:
        raise RetryableError(f"click on {what} blocked by {await covering_element(locator)}") from None


async def settle(page: Page) -> None:
    """Best-effort wait for network quiet; apps with live connections may never go idle."""
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)


async def wait_until(
    page: Page,
    guard: PopupGuard,
    predicate: Callable[[Page], bool | Awaitable[bool]],
    what: str,
    timeout_s: float = 30,
) -> None:
    """Poll ``predicate`` until true, clearing popups while waiting."""
    for _ in range(int(timeout_s * 1000 / POLL_MS)):
        result = predicate(page)
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await guard.dismiss_any(page)
        await page.wait_for_timeout(POLL_MS)
    raise RetryableError(f"timed out waiting for {what}")
