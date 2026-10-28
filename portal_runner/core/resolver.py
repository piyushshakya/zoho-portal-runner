"""Resilient element resolver.

A ``Target`` describes one element in several ways, tried in this order:
role + name, label/placeholder, nearby text, structure, then id/class.
The first strategy matching exactly one visible element wins; ambiguity falls
through. What users see changes rarely, so it is tried first; ids and classes
are build artefacts, so they come last. All frames are searched, every attempt
is recorded, and the same code works on saved HTML offline.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, replace

from playwright.async_api import ElementHandle, Frame, Locator, Page

log = logging.getLogger(__name__)

STRATEGY_ORDER = ("role", "label", "near_text", "structure", "css")

# `[tabindex='0']` also catches custom widgets (a <div> dropdown) without an ARIA role.
FIELDS = (
    "input:not([type=hidden]):not([type=button]):not([type=submit]), textarea, select, "
    "[role=combobox], [role=textbox], [contenteditable=true], "
    "[tabindex='0']:not(button):not(button *):not(a):not(input):not(textarea)"
)
BUTTONS = "button, [role=button], input[type=button], input[type=submit]"

MAX_ANCESTOR_HOPS = 5
MAX_ELEMENTS_CHECKED = 24
POLL_INTERVAL_MS = 300
DEFAULT_TIMEOUT_S = 10.0
BROKEN_MARKER = "__broken_on_purpose__"


@dataclass(frozen=True)
class Target:
    key: str
    role: str | None = None
    name: str | None = None
    label: str | None = None
    placeholder: str | None = None
    near_text: str | None = None
    structure: tuple[str, int] | None = None  # (selector, index among visible matches)
    css: tuple[str, ...] = ()

    def broken(self) -> Target:
        """A copy that matches nothing (fault injection for ``--break``)."""
        return replace(
            self,
            name=BROKEN_MARKER,
            label=BROKEN_MARKER,
            placeholder=None,
            near_text=BROKEN_MARKER,
            structure=(f"[data-{BROKEN_MARKER}]", 0),
            css=(f"#{BROKEN_MARKER}",),
        )


@dataclass(frozen=True)
class Attempt:
    strategy: str
    detail: str
    frame: str
    outcome: str  # match | none | ambiguous(n) | error:<Type> | skipped

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class Resolved:
    locator: Locator
    strategy: str
    detail: str
    frame: str


class ResolveError(Exception):
    """No strategy found exactly one visible element (retryable: the page may still be loading)."""

    def __init__(self, target: Target, attempts: list[Attempt]) -> None:
        self.target = target
        self.attempts = attempts
        tried = ", ".join(f"{a.strategy}={a.outcome}" for a in attempts if a.frame == "main")
        super().__init__(f"could not resolve '{target.key}' ({tried})")


def text_pattern(text: str) -> re.Pattern[str]:
    """Whole-text, case-insensitive; ignores asterisks and a trailing colon ('Name' ~ 'Name*:')."""
    return re.compile(r"^[\s*]*" + re.escape(text.strip()) + r"[\s*:]*$", re.IGNORECASE)


def _frame_label(frame: Frame, main: Frame) -> str:
    if frame == main:
        return "main"
    return f"iframe[{frame.name or frame.url[:40] or 'srcdoc'}]"


def _candidates(t: Target, strategy: str, f: Frame) -> list[tuple[str, Locator]]:
    if strategy == "role" and t.role and t.name:
        return [(f"role={t.role} name~{t.name!r}", f.get_by_role(t.role, name=text_pattern(t.name)))]  # type: ignore[arg-type]
    if strategy == "label":
        out = []
        if t.label:
            out.append((f"label~{t.label!r}", f.get_by_label(text_pattern(t.label))))
        if t.placeholder:
            out.append((f"placeholder~{t.placeholder!r}", f.get_by_placeholder(text_pattern(t.placeholder))))
        return out
    if strategy == "near_text" and t.near_text:
        anchor = f.get_by_text(text_pattern(t.near_text))
        wanted = BUTTONS if t.role == "button" else FIELDS
        return [
            (f"text~{t.near_text!r} +{hops} up", anchor.locator(f"xpath=ancestor::*[{hops}]").locator(wanted))
            for hops in range(1, MAX_ANCESTOR_HOPS + 1)
        ]
    if strategy == "structure" and t.structure:
        selector, index = t.structure
        return [(f"structure {selector} #{index}", f.locator(selector))]
    if strategy == "css":
        return [(f"css {selector}", f.locator(selector)) for selector in t.css]
    return []


async def _visible(loc: Locator) -> list[Locator]:
    n = min(await loc.count(), MAX_ELEMENTS_CHECKED)
    return [loc.nth(i) for i in range(n) if await loc.nth(i).is_visible()]


async def _distinct(locs: list[Locator]) -> list[Locator]:
    """near_text can reach one element from several anchors: keep one locator per element."""
    unique: list[Locator] = []
    handles: list[ElementHandle] = []
    try:
        for loc in locs:
            handle = await loc.element_handle()
            if handle is None:
                continue
            duplicate = False
            for seen in handles:
                if await handle.evaluate("(a, b) => a === b", seen):
                    duplicate = True
                    break
            if duplicate:
                await handle.dispose()
            else:
                handles.append(handle)
                unique.append(loc)
    finally:
        for handle in handles:
            await handle.dispose()
    return unique


class Resolver:
    def __init__(
        self,
        page: Page,
        *,
        default_timeout: float = DEFAULT_TIMEOUT_S,
        broken: frozenset[str] = frozenset(),
    ) -> None:
        self.page = page
        self.default_timeout = default_timeout
        self.broken = broken
        self.history: list[dict[str, str]] = []

    async def resolve(self, t: Target, *, timeout: float | None = None) -> Resolved:
        """Try every strategy in every frame, repeating until the timeout."""
        if t.key in self.broken:
            t = t.broken()
        deadline = time.monotonic() + (self.default_timeout if timeout is None else timeout)
        while True:
            attempts: list[Attempt] = []
            found = await self._one_pass(t, attempts)
            if found is not None:
                self.history.append(
                    {"key": t.key, "strategy": found.strategy, "detail": found.detail, "frame": found.frame}
                )
                log.info("resolver: %-24s -> %-9s (%s, %s)", t.key, found.strategy, found.detail, found.frame)
                return found
            if time.monotonic() >= deadline:
                log.warning("resolver: %s NOT FOUND after %d attempts", t.key, len(attempts))
                raise ResolveError(t, attempts)
            await self.page.wait_for_timeout(POLL_INTERVAL_MS)

    async def _one_pass(self, t: Target, attempts: list[Attempt]) -> Resolved | None:
        main = self.page.main_frame
        frames = [main, *(f for f in self.page.frames if f != main)]
        for strategy in STRATEGY_ORDER:
            for frame in frames:
                found = await self._try(t, strategy, frame, _frame_label(frame, main), attempts)
                if found is not None:
                    return found
        return None

    async def _try(
        self, t: Target, strategy: str, frame: Frame, frame_name: str, attempts: list[Attempt]
    ) -> Resolved | None:
        candidates = _candidates(t, strategy, frame)
        if not candidates:
            if frame_name == "main":
                attempts.append(Attempt(strategy, "not configured", frame_name, "skipped"))
            return None
        for detail, loc in candidates:
            try:
                visible = await _visible(loc)
            except Exception as e:  # detached frame, malformed selector
                attempts.append(Attempt(strategy, detail, frame_name, f"error:{type(e).__name__}"))
                continue
            if strategy == "structure" and t.structure is not None:
                index = t.structure[1]
                visible = [visible[index]] if len(visible) > index else []
            if strategy == "near_text" and len(visible) > 1:
                visible = await _distinct(visible)
            if len(visible) == 1:
                attempts.append(Attempt(strategy, detail, frame_name, "match"))
                return Resolved(visible[0], strategy, detail, frame_name)
            outcome = f"ambiguous({len(visible)})" if visible else "none"
            attempts.append(Attempt(strategy, detail, frame_name, outcome))
            if strategy == "near_text" and visible:
                break  # going further up only widens the scope
        return None
