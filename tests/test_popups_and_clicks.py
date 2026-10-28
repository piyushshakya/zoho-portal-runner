import re

import pytest

from portal_runner.core.actions import click
from portal_runner.core.errors import RetryableError
from portal_runner.core.popups import PopupGuard
from portal_runner.portals.zoho.popups import ZOHO_POPUPS

REVIEW_PROMPT = """
<button onclick="document.title='clicked'">Invoices</button>
<div id="ov" style="position:fixed;inset:0;background:#fff">
  <p>Did we make invoicing better for you?</p>
  <a href="#" onclick="document.getElementById('ov').remove();return false">Remind Me Later</a>
</div>
"""

UNKNOWN_OVERLAY = """
<button>Invoices</button>
<div class="mystery-mask" style="position:fixed;inset:0;background:rgba(0,0,0,.3)">Loading...</div>
"""


async def test_popup_is_cleared_before_the_click(offline_page):
    page = await offline_page(REVIEW_PROMPT)
    guard = PopupGuard(ZOHO_POPUPS)
    await guard.install(page)
    await page.get_by_role("button", name="Invoices").click(timeout=5000)
    assert await page.title() == "clicked"
    assert guard.dismissed == ["review_prompt"]


async def test_blocked_click_names_the_element_on_top(offline_page):
    page = await offline_page(UNKNOWN_OVERLAY)
    with pytest.raises(RetryableError, match=re.compile("mystery-mask.*Loading", re.S)):
        await click(page, page.get_by_role("button", name="Invoices"), PopupGuard(), "nav.invoices", timeout_ms=600)
