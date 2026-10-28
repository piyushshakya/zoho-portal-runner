"""Make "the portal changed its front-end" copies of a saved page.

    python -m portal_runner mutate in.html out.html --all [--strip-aria]

Mutations: rename classes / ids, reverse sibling order, wrap fields in extra divs,
move the page into an iframe; ``strip_aria`` (not in --all) removes roles, aria-*
and label links to force the resolver onto its fallback strategies.
Edits run on the parsed DOM in a browser, so the output stays well-formed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from pathlib import Path

from playwright.async_api import async_playwright

from portal_runner.core.offline import open_offline

STANDARD = ("classes", "ids", "reorder", "wrap", "iframe")
ALL_MUTATIONS = (*STANDARD, "strip_aria")

_JS = r"""
(opts) => {
  let seed = opts.seed || 7;
  const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed.toString(36); };
  const body = document.body;

  if (opts.classes) {
    const map = new Map();
    const pick = (c) => { if (!map.has(c)) map.set(c, 'm' + rnd() + map.size); return map.get(c); };
    for (const el of document.querySelectorAll('[class]')) {
      const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean);
      el.setAttribute('class', cls.map(pick).join(' '));
    }
  }

  if (opts.ids) {
    const map = new Map();
    for (const el of document.querySelectorAll('[id]')) {
      const n = 'x' + rnd() + map.size; map.set(el.id, n); el.id = n;
    }
    const refAttrs = ['for', 'aria-labelledby', 'aria-describedby', 'aria-controls',
                      'aria-owns', 'aria-activedescendant', 'form', 'list'];
    for (const a of refAttrs) {
      for (const el of document.querySelectorAll('[' + a + ']')) {
        const v = el.getAttribute(a).split(/\s+/).map(t => map.get(t) || t).join(' ');
        el.setAttribute(a, v);
      }
    }
  }

  if (opts.strip_aria) {
    for (const el of document.querySelectorAll('*')) {
      for (const a of [...el.attributes]) {
        if (a.name === 'role' || a.name.startsWith('aria-') || (el.tagName === 'LABEL' && a.name === 'for'))
          el.removeAttribute(a.name);
      }
    }
  }

  if (opts.reorder) {
    for (const el of [body, ...body.querySelectorAll('*')]) {
      const kids = [...el.children];
      if (kids.length < 2) continue;
      for (const k of kids.reverse()) el.appendChild(k);
    }
  }

  if (opts.wrap) {
    const sel = 'input:not([type=hidden]), textarea, select, button, label, [role=combobox], [role=textbox]';
    for (const el of [...body.querySelectorAll(sel)]) {
      const w = document.createElement('div');
      w.setAttribute('data-added-wrapper', '');
      el.parentNode.insertBefore(w, el);
      w.appendChild(el);
    }
    const outer = document.createElement('div');
    outer.setAttribute('data-added-wrapper', 'outer');
    while (body.firstChild) outer.appendChild(body.firstChild);
    body.appendChild(outer);
  }

  if (opts.iframe) {
    const inner = '<!doctype html><html><head><meta charset="utf-8"></head><body>' + body.innerHTML + '</body></html>';
    body.innerHTML = '';
    const f = document.createElement('iframe');
    f.setAttribute('name', 'mutated-frame');
    f.setAttribute('style', 'width:1440px;height:3000px;border:0');
    f.setAttribute('srcdoc', inner);
    body.appendChild(f);
  }
  return '<!doctype html>\n' + document.documentElement.outerHTML;
}
"""


async def mutate(html: str, mutations: Iterable[str], seed: int = 7) -> str:
    unknown = set(mutations) - set(ALL_MUTATIONS)
    if unknown:
        raise ValueError(f"unknown mutations: {sorted(unknown)}")
    options = {m: m in set(mutations) for m in ALL_MUTATIONS} | {"seed": seed}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await open_offline(browser, html)
        out: str = await page.evaluate(_JS, options)
        await browser.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mutate saved HTML to test the resolver.")
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path)
    for name in ALL_MUTATIONS:
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, action="store_true")
    parser.add_argument("--all", action="store_true", help=f"all of: {', '.join(STANDARD)}")
    args = parser.parse_args(argv)
    chosen = [m for m in ALL_MUTATIONS if getattr(args, m) or (args.all and m in STANDARD)]
    args.dst.write_text(asyncio.run(mutate(args.src.read_text(encoding="utf-8"), chosen)), encoding="utf-8")
    print(f"wrote {args.dst} ({', '.join(chosen) or 'no mutations'})")
    return 0
