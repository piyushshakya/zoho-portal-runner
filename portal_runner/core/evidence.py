"""Failure evidence bundle, scrubbed before anything is written.

runs/<id>/evidence/: screenshot.png, page.html, url.txt, console.log, error.json
(stage, error, retryable?, attempts, resolver attempts, redaction report).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from portal_runner.core.redact import EMAIL, Redactor
from portal_runner.core.resolver import ResolveError
from portal_runner.core.retry import is_retryable

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000
SCRIPT_BLOCK = re.compile(r"<script\b.*?</script>", re.S | re.I)

# Runs in the page before the screenshot: rewrites text/attributes in the document and
# every open shadow root (Zoho's invoice preview lives in one) and blanks secret inputs.
_SCRUB_JS = r"""
(secrets) => {
  const patterns = secrets.map(s => new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'));
  const clean = (s) => {
    for (const p of patterns) s = s.replace(p, '[REDACTED]');
    return s.replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, '[EMAIL]')
            .replace(/(?<=%[0-9A-Fa-f]{2})\d{10,}/g, '[ID]')
            .replace(/(?<![\da-f])\d{10,}(?![\da-f])/g, '[ID]');
  };
  let shadowRoots = 0;
  const visit = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) n.nodeValue = clean(n.nodeValue);
    for (const el of root.querySelectorAll('*')) {
      for (const a of [...el.attributes]) { const v = clean(a.value); if (v !== a.value) el.setAttribute(a.name, v); }
      if (el.matches('input, textarea')) {
        const secret = el.type === 'password' || el.autocomplete === 'one-time-code'
                       || /otp|pass/i.test(el.name + el.id + el.className);
        el.value = secret ? '' : clean(el.value);
        if (secret) el.setAttribute('value', '');
      }
      if (el.shadowRoot) { shadowRoots++; visit(el.shadowRoot); }
    }
  };
  visit(document);
  return shadowRoots;
}
"""

# After scrubbing: does any email or known secret remain anywhere (shadow DOM included)?
_LEAK_CHECK_JS = r"""
(secrets) => {
  const texts = [];
  const visit = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) texts.push(n.nodeValue);
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) visit(el.shadowRoot);
  };
  visit(document);
  const all = texts.join(' ').toLowerCase();
  return /[\w.+-]+@[\w-]+\.[\w.-]+/.test(all) || secrets.some(s => all.includes(s.toLowerCase()));
}
"""


async def _scrub_page(page: Page, redactor: Redactor) -> dict[str, Any]:
    shadow_roots, leaks, unreachable = 0, [], []
    for frame in page.frames:
        name = "main" if frame == page.main_frame else frame.name or "iframe"
        try:
            shadow_roots += await frame.evaluate(_SCRUB_JS, redactor.secrets)
            if await frame.evaluate(_LEAK_CHECK_JS, redactor.secrets):
                leaks.append(name)
        except Exception as e:  # e.g. a cross-origin frame
            unreachable.append(f"{name} ({type(e).__name__})")
    return {"shadow_roots_scrubbed": shadow_roots, "leftovers_after_scrub": leaks, "frames_not_scriptable": unreachable}


async def _capture_page(page: Page, out: Path, redactor: Redactor) -> dict[str, Any]:
    url = redactor.url(page.url)
    (out / "url.txt").write_text(url + "\n", encoding="utf-8")
    redaction = await _scrub_page(page, redactor)
    # Last line of defence for the image: paint over pictures (avatars) and any leftover secret.
    masks = [page.locator("img"), page.get_by_text(EMAIL)]
    if redactor.secrets:
        masks.append(page.get_by_text(re.compile("|".join(map(re.escape, redactor.secrets)), re.I)))
    await page.screenshot(path=str(out / "screenshot.png"), full_page=True, mask=masks, mask_color="#444")
    # Inline scripts carry the app's account JSON and are not needed for debugging.
    html = SCRIPT_BLOCK.sub("<script></script>", await page.content())
    (out / "page.html").write_text(redactor.text(html), encoding="utf-8")
    return {"url": url, "redaction": redaction}


async def write_bundle(
    run_dir: Path,
    page: Page | None,
    *,
    stage: str | None,
    error: BaseException,
    redactor: Redactor,
    console: list[str],
    attempts: int,
) -> Path:
    out = Path(run_dir) / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {
        "stage": stage,
        "error_type": type(error).__name__,
        "message": redactor.text(str(error))[:MAX_MESSAGE_CHARS],
        "retryable": is_retryable(error),
        "attempts": attempts,
    }
    if isinstance(error, ResolveError):
        info["target"] = error.target.key
        info["resolver_attempts"] = [a.as_dict() for a in error.attempts]

    if page is not None and not page.is_closed():
        try:
            info.update(await _capture_page(page, out, redactor))
        except Exception as e:  # evidence must never hide the original failure
            info["evidence_error"] = redactor.text(f"{type(e).__name__}: {e}")[:300]
    (out / "console.log").write_text("\n".join(map(redactor.text, console)) + "\n", encoding="utf-8")
    (out / "error.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    log.error("evidence bundle written to %s", out)
    return out
