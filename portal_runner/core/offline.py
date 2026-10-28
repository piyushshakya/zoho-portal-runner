"""Run the resolver against saved HTML with the network switched off.

    python -m portal_runner offline page.html [more.html ...] [--portal zoho_invoice] [--json]

Prints which strategy found each target; exit code 1 if any target is not found.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, async_playwright

from portal_runner.core.resolver import ResolveError, Resolver, Target

OFFLINE_TIMEOUT_S = 0.5


async def open_offline(browser: Browser, html: str) -> Page:
    """A page that cannot reach the network (iframes via srcdoc still load)."""
    context = await browser.new_context(offline=True)
    page = await context.new_page()
    await page.route("**/*", lambda route: route.abort())
    await page.set_content(html, wait_until="domcontentloaded")
    return page


async def resolve_all(page: Page, targets: Sequence[Target]) -> list[dict[str, Any]]:
    resolver = Resolver(page, default_timeout=OFFLINE_TIMEOUT_S)
    rows = []
    for t in targets:
        try:
            r = await resolver.resolve(t)
            rows.append({"key": t.key, "found": True, "strategy": r.strategy, "detail": r.detail, "frame": r.frame})
        except ResolveError as e:
            rows.append({"key": t.key, "found": False, "attempts": [a.as_dict() for a in e.attempts]})
    return rows


def _print_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    print(f"\n{path}")
    for r in rows:
        if r["found"]:
            print(f"  OK    {r['key']:22} via {r['strategy']:9} {r['detail']}  [{r['frame']}]")
        else:
            tried = ", ".join(f"{a['strategy']}={a['outcome']}" for a in r["attempts"] if a["frame"] == "main")
            print(f"  FAIL  {r['key']:22} tried: {tried}")


async def main(argv: Sequence[str], targets_for: Callable[[str], Sequence[Target]]) -> int:
    """``targets_for(portal_name)`` is injected by the CLI so the core never imports portals."""
    parser = argparse.ArgumentParser(description="Run the resolver on saved HTML, offline.")
    parser.add_argument("html", nargs="+", type=Path)
    parser.add_argument("--portal", default="zoho_invoice")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    targets = targets_for(args.portal)
    all_found = True
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for path in args.html:
            page = await open_offline(browser, path.read_text(encoding="utf-8"))
            rows = await resolve_all(page, targets)
            await page.context.close()
            all_found &= all(r["found"] for r in rows)
            if args.json:
                print(json.dumps({"file": str(path), "results": rows}, indent=1))
            else:
                _print_rows(path, rows)
        await browser.close()
    return 0 if all_found else 1
