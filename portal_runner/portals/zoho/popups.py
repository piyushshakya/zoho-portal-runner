"""Zoho popups that can interrupt any step, each with its harmless way out."""

from __future__ import annotations

import re

from playwright.async_api import Locator, Page

from portal_runner.core.popups import Popup

CLICK_TIMEOUT_MS = 5000


def _text(page: Page, pattern: str) -> Locator:
    return page.get_by_text(re.compile(pattern, re.IGNORECASE))


async def _remind_me_later(page: Page) -> None:
    await _text(page, r"^\s*remind me later\s*$").first.click(timeout=CLICK_TIMEOUT_MS)


async def _this_invoice_only(page: Page) -> None:
    # "Enter the number for this invoice but resume auto-generating ...": changes no settings.
    await _text(page, r"for this invoice but resume").first.click(timeout=CLICK_TIMEOUT_MS)
    await page.get_by_role("button", name=re.compile(r"^\s*save\s*$", re.I)).first.click(timeout=CLICK_TIMEOUT_MS)


async def _not_now(page: Page) -> None:
    # Not "Trust": a trusted browser would skip 2FA on the next full login.
    await page.get_by_role("button", name=re.compile(r"^\s*not now\s*$", re.I)).first.click(timeout=CLICK_TIMEOUT_MS)


ZOHO_POPUPS = (
    Popup("review_prompt", lambda p: _text(p, r"did we make invoicing better"), _remind_me_later),
    Popup(
        "invoice_number_preferences", lambda p: _text(p, r"configure invoice number preferences"), _this_invoice_only
    ),
    Popup("trust_this_browser", lambda p: _text(p, r"^\s*trust this browser\??\s*$"), _not_now),
)
