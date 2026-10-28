"""Full Zoho login: email -> password -> TOTP -> interstitials -> app."""

from __future__ import annotations

import re
from collections.abc import Callable

from playwright.async_api import Locator, Page

from portal_runner.core.actions import click
from portal_runner.core.codes import CodeProvider, submit_with_one_retry
from portal_runner.core.errors import FinalError, RetryableError
from portal_runner.core.handoff import wait_for_human
from portal_runner.portals.base import FlowContext
from portal_runner.portals.zoho import targets
from portal_runner.portals.zoho.config import ZohoConfig

FIELD_TIMEOUT_S = 20
VERIFY_POLL_MS = 500
VERIFY_POLLS = 30
INTERSTITIAL_POLLS = 30
KEY_DELAY_MS = 80
AFTER_SIGN_IN_MS = 1500  # let Zoho swap in the next screen (TOTP, OneAuth, CAPTCHA)
SKIP_BUTTON = re.compile(r"^\s*(not now|remind me later|skip|later)\s*$", re.I)


async def _visible(loc: Locator) -> bool:
    try:
        return await loc.first.is_visible()
    except Exception:
        return False


async def _handle_captcha(ctx: FlowContext) -> None:
    """Never solved automatically: handed to a person when the browser is visible, else a final error."""
    captcha = ctx.page.get_by_placeholder(re.compile(r"enter captcha", re.I))
    if not await _visible(captcha):
        return
    if ctx.human_wait_s <= 0:
        raise FinalError("Zoho is showing a CAPTCHA; run with --headed to solve it by hand")
    waited = await wait_for_human(ctx.page, lambda: _visible(captcha), "Zoho CAPTCHA", ctx.human_wait_s)
    ctx.record.manifest.setdefault("human_handoffs", []).append({"challenge": "captcha", "waited_s": round(waited)})


async def _fail_if_blocked(page: Page) -> None:
    if await _visible(page.get_by_text(re.compile(r"incorrect password|invalid password", re.I))):
        raise FinalError("Zoho rejected the password (check ZOHO_PASSWORD)")


async def _choose_authenticator_method(page: Page) -> None:
    """Zoho may default to OneAuth push approval; switch to the authenticator app."""
    other = page.get_by_text(re.compile(r"sign in another way|(another|other) (way|method)", re.I))
    if await _visible(other):
        await other.first.click()
        await page.get_by_text(re.compile(r"TOTP|authenticator|time.?based", re.I)).first.click()


async def _skip_interstitial(ctx: FlowContext) -> None:
    """Known popups first, then any generic 'Skip' / 'Not now' button (recovery prompts etc.)."""
    if not await ctx.guard.dismiss_any(ctx.page):
        skip = ctx.page.get_by_role("button", name=SKIP_BUTTON)
        if await _visible(skip):
            await skip.first.click()


async def full_login(
    ctx: FlowContext, config: ZohoConfig, codes: CodeProvider, is_app_url: Callable[[str], bool]
) -> None:
    await ctx.page.goto(config.app_url, wait_until="domcontentloaded")
    ctx.record.visit("login", ctx.page.url)
    await _enter_credentials(ctx, config)
    async with ctx.record.stage("login.second_factor"):
        await _enter_code(ctx, codes)
    for _ in range(INTERSTITIAL_POLLS):
        if is_app_url(ctx.page.url):
            break
        await _skip_interstitial(ctx)
        await ctx.page.wait_for_timeout(VERIFY_POLL_MS * 2)
    else:
        raise RetryableError("login finished but the app never opened")
    await ctx.resolver.resolve(targets.NAV_INVOICES, timeout=FIELD_TIMEOUT_S)
    ctx.record.visit("dashboard", ctx.page.url)


async def _enter_credentials(ctx: FlowContext, config: ZohoConfig) -> None:
    page, r = ctx.page, ctx.resolver
    await (await r.resolve(targets.LOGIN_EMAIL, timeout=FIELD_TIMEOUT_S)).locator.fill(config.email)
    await click(page, (await r.resolve(targets.LOGIN_NEXT)).locator, ctx.guard, targets.LOGIN_NEXT.key)
    await (await r.resolve(targets.LOGIN_PASSWORD, timeout=FIELD_TIMEOUT_S)).locator.fill(config.password)
    await click(page, (await r.resolve(targets.LOGIN_SIGN_IN)).locator, ctx.guard, targets.LOGIN_SIGN_IN.key)
    await page.wait_for_timeout(AFTER_SIGN_IN_MS)
    await _handle_captcha(ctx)
    await _fail_if_blocked(page)
    await _choose_authenticator_method(page)


async def _enter_code(ctx: FlowContext, codes: CodeProvider) -> None:
    """Zoho splits the code over six one-digit boxes; they are found from the first one."""
    page = ctx.page
    first_box = (await ctx.resolver.resolve(targets.LOGIN_TOTP, timeout=FIELD_TIMEOUT_S)).locator
    boxes = first_box.locator("xpath=..").locator("input:visible")
    verify = (await ctx.resolver.resolve(targets.LOGIN_VERIFY)).locator
    rejected = page.get_by_text(re.compile(r"invalid|incorrect|wrong|expired", re.I))

    async def submit(code: str) -> bool:
        ctx.redactor.add(code)
        for i in range(await boxes.count()):
            await boxes.nth(i).fill("")
        await boxes.first.click()
        await page.keyboard.type(code, delay=KEY_DELAY_MS)
        await verify.click()
        for _ in range(VERIFY_POLLS):
            await page.wait_for_timeout(VERIFY_POLL_MS)
            if not await _visible(first_box):
                return True
            if await _visible(rejected):
                return False
        return False

    await submit_with_one_retry(codes, submit, portal="zoho")
