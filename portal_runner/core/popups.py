"""Popups that can appear at any moment and block clicks.

``install`` registers each popup with ``page.add_locator_handler`` so Playwright
clears it before every action; ``dismiss_any`` checks once, for our own wait loops
where no action runs. Handlers always take the harmless way out ("Not now").
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from playwright.async_api import Locator, Page

log = logging.getLogger(__name__)

GONE_TIMEOUT_MS = 4000


@dataclass(frozen=True)
class Popup:
    name: str
    detector: Callable[[Page], Locator]
    handle: Callable[[Page], Awaitable[None]]


class PopupGuard:
    def __init__(self, popups: Sequence[Popup] = ()) -> None:
        self.popups = list(popups)
        self.dismissed: list[str] = []
        self._busy = False

    async def install(self, page: Page) -> None:
        for popup in self.popups:

            async def handler(*_: object, popup: Popup = popup) -> None:
                await self._handle(page, popup)

            await page.add_locator_handler(popup.detector(page).first, handler, no_wait_after=True)

    async def dismiss_any(self, page: Page) -> bool:
        for popup in self.popups:
            try:
                visible = await popup.detector(page).first.is_visible()
            except Exception as e:  # page navigating away mid-check
                log.debug("popup: could not check %s (%s)", popup.name, type(e).__name__)
                continue
            if visible:
                await self._handle(page, popup)
                return True
        return False

    async def _handle(self, page: Page, popup: Popup) -> None:
        # Clicks inside a handler are actions too and would re-trigger it.
        if self._busy:
            return
        self._busy = True
        try:
            log.info("popup: handling %s", popup.name)
            if not self.dismissed or self.dismissed[-1] != popup.name:
                self.dismissed.append(popup.name)
            await popup.handle(page)
            # Wait out the fade-out, or the next action sees it again.
            await popup.detector(page).first.wait_for(state="hidden", timeout=GONE_TIMEOUT_MS)
        except Exception as e:
            # A handler must never crash the action it protects (the popup may have closed itself).
            log.warning("popup: %s handler did not complete (%s)", popup.name, type(e).__name__)
        finally:
            self._busy = False
