from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from playwright.async_api import Browser, Page, async_playwright

from portal_runner.core.offline import open_offline


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        yield b
        await b.close()


@pytest.fixture
def offline_page(browser: Browser) -> Callable[[str], Awaitable[Page]]:
    """Load HTML into a page with the network blocked."""

    async def load(html: str) -> Page:
        return await open_offline(browser, html)

    return load
