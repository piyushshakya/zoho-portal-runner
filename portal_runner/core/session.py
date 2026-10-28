"""Save, reuse and validate the logged-in browser state.

A session is dead if any of three signals says so, cheapest first:
1. preflight: the file is missing, corrupt, or all portal cookies have expired;
2. redirect: opening the app lands on the login host;
3. marker: an element only logged-in users see never renders (catches empty shells).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from portal_runner.core.resolver import ResolveError, Resolver, Target

log = logging.getLogger(__name__)

STATE_FILE_MODE = 0o600  # holds live auth cookies
PROBE_TIMEOUT_S = 20.0
PROBE_POLL_MS = 300
NO_EXPIRY = -1  # Playwright's value for session cookies


def host(url: str) -> str:
    """Compare hostnames, never whole URLs: login URLs carry the app URL in their query."""
    return urlparse(url).hostname or ""


@dataclass(frozen=True)
class Verdict:
    alive: bool
    reason: str


@dataclass(frozen=True)
class SessionRules:
    app_url: str
    is_app_url: Callable[[str], bool]
    is_login_url: Callable[[str], bool]
    logged_in_marker: Target
    cookie_domain: str


class SessionStore:
    def __init__(self, path: Path, cookie_domain: str) -> None:
        self.path = Path(path)
        self.cookie_domain = cookie_domain

    def preflight(self, now: float | None = None) -> Verdict:
        now = time.time() if now is None else now
        if not self.path.exists():
            return Verdict(alive=False, reason="no saved session")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Verdict(alive=False, reason="saved session is not valid JSON (corrupt)")
        cookies = data.get("cookies") if isinstance(data, dict) else None
        if not isinstance(cookies, list) or not cookies:
            return Verdict(alive=False, reason="saved session has no cookies")
        ours = [c for c in cookies if isinstance(c, dict) and self.cookie_domain in str(c.get("domain", ""))]
        if not ours:
            return Verdict(alive=False, reason=f"saved session has no {self.cookie_domain} cookies")
        live = [c for c in ours if c.get("expires", NO_EXPIRY) in (NO_EXPIRY, None) or c["expires"] > now]
        if not live:
            return Verdict(alive=False, reason="all saved cookies have expired")
        return Verdict(alive=True, reason=f"{len(live)} cookies look valid")

    async def save(self, ctx: BrowserContext) -> None:
        """Atomic (no half-written file on crash) and private (0600)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        await ctx.storage_state(path=str(tmp))
        os.chmod(tmp, STATE_FILE_MODE)
        os.replace(tmp, self.path)
        log.info("session: saved to %s", self.path)

    def discard(self, reason: str) -> None:
        """Move a dead session aside for debugging."""
        if self.path.exists():
            dead = self.path.with_name(self.path.stem + ".dead.json")
            os.replace(self.path, dead)
            log.info("session: discarded (%s) -> %s", reason, dead.name)


async def probe(page: Page, rules: SessionRules, resolver: Resolver, timeout_s: float = PROBE_TIMEOUT_S) -> Verdict:
    """Signals 2 and 3: open the app, then check where we land and what renders."""
    await page.goto(rules.app_url, wait_until="domcontentloaded")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:  # redirects may happen later, in JavaScript
        if rules.is_login_url(page.url):
            return Verdict(alive=False, reason="redirected to the login page")
        if rules.is_app_url(page.url):
            try:
                await resolver.resolve(rules.logged_in_marker, timeout=max(1.0, deadline - time.monotonic()))
            except ResolveError:
                if rules.is_login_url(page.url):
                    return Verdict(alive=False, reason="redirected to the login page")
                return Verdict(alive=False, reason="app opened but the logged-in navigation never rendered")
            return Verdict(alive=True, reason="app shell rendered with logged-in navigation")
        await page.wait_for_timeout(PROBE_POLL_MS)
    return Verdict(alive=False, reason=f"unexpected host {host(page.url)!r}")
