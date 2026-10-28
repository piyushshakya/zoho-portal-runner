"""Save a sanitized copy of a live Zoho page as a test fixture (uses the saved session).

    python tools/capture_fixture.py "#/invoices/new" tests/fixtures/invoice_new.html

Scripts are removed and secrets/ids redacted, so the file is safe to commit.
Run the main program once first so state/zoho_invoice.json exists.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from portal_runner.core.actions import settle
from portal_runner.core.evidence import SCRIPT_BLOCK
from portal_runner.core.popups import PopupGuard
from portal_runner.core.redact import Redactor
from portal_runner.portals.zoho.config import load_config
from portal_runner.portals.zoho.popups import ZOHO_POPUPS

RENDER_WAIT_MS = 3000


async def capture(route: str, out: Path, state: Path) -> None:
    config = load_config()
    redactor = Redactor([config.email, config.password, config.totp_secret, *config.redact_names])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context(storage_state=str(state))).new_page()
        await PopupGuard(ZOHO_POPUPS).install(page)
        await page.goto(config.app_url + route, wait_until="domcontentloaded")
        await settle(page)
        await page.wait_for_timeout(RENDER_WAIT_MS)
        html = SCRIPT_BLOCK.sub("", await page.content())
        await browser.close()
    out.write_text(redactor.text(html), encoding="utf-8")
    print(f"saved {out} ({len(html) // 1024} KB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("route", help='app route, e.g. "#/invoices/new"')
    parser.add_argument("out", type=Path)
    parser.add_argument("--state", type=Path, default=Path("state/zoho_invoice.json"))
    args = parser.parse_args()
    asyncio.run(capture(args.route, args.out, args.state))


if __name__ == "__main__":
    main()
