"""Dashboard -> Invoices -> (existing invoice | New Invoice form) -> PDF download.

Idempotent: the invoice number is the key. If that invoice exists it is opened
instead of created, so a second run returns the same document, faster.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Locator, Page

from portal_runner.core.actions import click, settle, wait_until
from portal_runner.core.documents import content_fingerprint, flatten, pdf_text
from portal_runner.core.errors import FinalError, RetryableError
from portal_runner.core.resolver import Target, text_pattern
from portal_runner.portals.base import FlowContext
from portal_runner.portals.zoho import targets
from portal_runner.portals.zoho.inputs import InvoiceInput, Item, expected_due_date, zoho_date

log = logging.getLogger(__name__)

DETAIL_PAGE = re.compile(r"#/invoices/\d+")
TYPE_DELAY_MS = 40
VALUE_POLLS = 24
VALUE_POLL_MS = 250
NAV_CLICK_TIMEOUT_MS = 2500
LIST_WAIT_S = 6
FORM_WAIT_S = 20
OPTION_TIMEOUT_MS = 10_000
EXISTING_LINK_MS = 3000
SEARCH_FOCUS_MS = 300
SEARCH_RESULT_MS = 6000
SAVE_WAIT_S = 40
DOWNLOAD_TIMEOUT_MS = 30_000
EMPTY_RATES = ("", "0", "0.00")


def fragment(url: str) -> str:
    return url.partition("#")[2].split("?")[0]


async def tag_row(field: Locator, row: int) -> bool:
    """Mark the <tr> holding ``field`` with our own attribute (Zoho's row numbers don't match the screen)."""
    return await field.evaluate(
        "(e, i) => { const tr = e.closest('tr'); if (tr) tr.setAttribute('data-runner-row', String(i)); return !!tr; }",
        row,
    )


async def row_fields(page: Page, row: int) -> tuple[Locator, Locator] | None:
    """(rate, quantity) inside the tagged row, found by role + name, or None if there is no such row."""
    tr = page.locator(f"tr[data-runner-row='{row}']")
    if await tr.count() != 1:
        return None
    return (
        tr.get_by_role("textbox", name=text_pattern("Rate")).first,
        tr.get_by_role("textbox", name=text_pattern("Quantity")).first,
    )


def verify_invoice_text(text: str, inv: InvoiceInput) -> list[str]:
    """Problems found when checking the PDF text against the inputs (empty = all good)."""
    flat = flatten(text)
    problems = []
    if inv.number not in flat:
        problems.append(f"invoice number {inv.number} missing")
    if inv.customer.lower() not in flat.lower():
        problems.append(f"customer {inv.customer!r} missing")
    for item in inv.items:
        qty = f"{item.quantity:,.2f}"
        # name, then its quantity, not glued to other digits (1.00 must not match 11.00)
        if not re.search(re.escape(item.name) + r".{0,40}?(?<![\d.,])" + re.escape(qty) + r"(?!\d)", flat, re.I):
            problems.append(f"item {item.name!r} with quantity {qty} missing")
    return problems


class InvoiceFlow:
    def __init__(self, ctx: FlowContext, inv: InvoiceInput) -> None:
        self.ctx, self.page, self.inv = ctx, ctx.page, inv
        self.manifest = ctx.record.manifest
        self.manifest.setdefault("navigation_fallbacks", [])

    async def run(self) -> Path:
        step = self.ctx.record.step
        await step("navigate.invoices", self.open_invoices, attempts=3)
        exists = await step("find_existing", self.find_existing, attempts=2, reset=self.back_to_list)
        if not exists:
            await step("navigate.new_invoice", self.open_new_form, attempts=3, reset=self.back_to_list)
            await step("fill_form", self.fill_form, attempts=2, reset=self.reload_form)
            await step("save", self.save)  # never retried: a second Save could duplicate the invoice
        self.manifest["invoice_action"] = "reused existing" if exists else "created"
        return await step("capture.pdf", self.download_pdf, attempts=3, reset=self.reload_page)

    # -- helpers -------------------------------------------------------------
    async def resolve(self, t: Target, timeout: float | None = None) -> Locator:
        return (await self.ctx.resolver.resolve(t, timeout=timeout)).locator

    async def click(self, t: Target, timeout: float | None = None) -> None:
        await click(self.page, await self.resolve(t, timeout), self.ctx.guard, t.key)

    async def wait_for(
        self, predicate: Callable[[Page], bool | Awaitable[bool]], what: str, timeout_s: float = 30
    ) -> None:
        await wait_until(self.page, self.ctx.guard, predicate, what, timeout_s)

    async def goto(self, frag: str) -> None:
        await self.page.goto(self.page.url.split("#")[0] + "#" + frag, wait_until="domcontentloaded")

    async def back_to_list(self) -> None:
        await self.goto("/invoices")

    async def reload_form(self) -> None:
        await self.goto("/invoices/new")
        await self.resolve(targets.CUSTOMER, FORM_WAIT_S)

    async def reload_page(self) -> None:
        await self.page.keyboard.press("Escape")
        await self.page.reload(wait_until="domcontentloaded")

    async def pick_option(self, text: str, what: str) -> None:
        """Choose ``text`` from the open dropdown (Zoho's custom listbox)."""
        option = self.page.get_by_role("option").filter(has_text=re.compile(re.escape(text), re.I)).first
        try:
            await option.wait_for(state="visible", timeout=OPTION_TIMEOUT_MS)
        except Exception:
            raise FinalError(f"{what} {text!r} is not in the dropdown; check inputs.json") from None
        await click(self.page, option, self.ctx.guard, f"{what} option {text!r}")

    async def type_and_pick(self, t: Target, text: str, what: str) -> Locator:
        box = await self.resolve(t)
        await click(self.page, box, self.ctx.guard, t.key)
        await self.page.keyboard.type(text, delay=TYPE_DELAY_MS)
        await self.pick_option(text, what)
        return box

    def navigation_fallback(self, target: str, reason: Exception) -> None:
        log.warning("navigation fallback for %s: %s", target, reason)
        self.manifest["navigation_fallbacks"].append({"target": target, "reason": str(reason)})

    # -- stages --------------------------------------------------------------
    async def open_invoices(self) -> None:
        def on_list(p: Page) -> bool:
            return fragment(p.url).startswith("/invoices")

        try:
            # Zoho quirk: its own sidebar container covers the links for a while after load.
            # Mouse first, then keyboard (inside click()), then the URL as a recorded fallback.
            link = await self.resolve(targets.NAV_INVOICES)
            await click(
                self.page,
                link,
                self.ctx.guard,
                targets.NAV_INVOICES.key,
                timeout_ms=NAV_CLICK_TIMEOUT_MS,
                keyboard_fallback=True,
            )
            await self.wait_for(on_list, "the invoice list", LIST_WAIT_S)
        except RetryableError as e:
            self.navigation_fallback(targets.NAV_INVOICES.key, e)
            await self.back_to_list()
            await self.wait_for(on_list, "the invoice list")
        await settle(self.page)
        self.ctx.record.visit("invoices_list", self.page.url)

    async def find_existing(self) -> bool:
        """Open the invoice if it exists: look in the list, then search with Zoho's '/' shortcut
        (the search box is collapsed until focused)."""
        page = self.page
        link = page.get_by_role("link", name=text_pattern(self.inv.number)).first
        try:
            await link.wait_for(state="visible", timeout=EXISTING_LINK_MS)
        except Exception:
            try:
                await page.keyboard.press("/")
                await page.wait_for_timeout(SEARCH_FOCUS_MS)
                await page.keyboard.type(self.inv.number, delay=TYPE_DELAY_MS)
                await page.keyboard.press("Enter")
                await link.wait_for(state="visible", timeout=SEARCH_RESULT_MS)
            except Exception:
                await page.keyboard.press("Escape")
                self.manifest["find_existing"] = "not found -> create"
                return False
        await click(page, link, self.ctx.guard, f"invoice {self.inv.number}")
        await self.wait_for(lambda p: bool(DETAIL_PAGE.search(p.url)), "the invoice page")
        self.ctx.record.visit("invoice_detail", page.url)
        self.manifest["find_existing"] = "exists -> reuse"
        return True

    async def open_new_form(self) -> None:
        if fragment(self.page.url) != "/invoices":
            await self.back_to_list()
        try:
            await self.click(targets.NEW_INVOICE)
        except RetryableError as e:
            self.navigation_fallback(targets.NEW_INVOICE.key, e)
            await self.goto("/invoices/new")
        await self.wait_for(lambda p: fragment(p.url).startswith("/invoices/new"), "the new invoice form")
        await self.resolve(targets.CUSTOMER, FORM_WAIT_S)
        self.ctx.record.visit("new_invoice_form", self.page.url)

    async def fill_form(self) -> None:
        self.manifest["dependent_fields"] = []  # fresh on every attempt
        await self.type_and_pick(targets.CUSTOMER, self.inv.customer, "customer")
        await self.set_number()
        date_box = await self.resolve(targets.INVOICE_DATE)
        await date_box.fill(zoho_date(self.inv.invoice_date))
        await date_box.press("Tab")
        await self.set_terms()
        for row, item in enumerate(self.inv.items):
            await self.add_item(row, item)
        if self.inv.notes:
            await (await self.resolve(targets.NOTES)).fill(self.inv.notes)

    async def set_number(self) -> None:
        # Leaving the field can open "Invoice Number Preferences"; the popup guard answers it.
        box = await self.resolve(targets.INVOICE_NUMBER)
        await box.fill(self.inv.number)
        await box.press("Tab")
        await self.page.wait_for_timeout(VALUE_POLL_MS * 2)
        await self.ctx.guard.dismiss_any(self.page)

    async def set_terms(self) -> None:
        """Terms drive Due Date (dependent field): pick terms, then wait for Zoho to recompute."""
        await self.click(targets.TERMS)
        await self.pick_option(self.inv.terms, "payment terms")
        due_box = await self.resolve(targets.DUE_DATE)
        expected = expected_due_date(self.inv.invoice_date, self.inv.terms)
        want = zoho_date(expected) if expected else None
        observed = await self.poll_value(due_box, lambda v: want is None or v == want)
        if want and observed != want:
            raise RetryableError(f"Due Date did not follow terms {self.inv.terms!r}: {observed} != {want}")
        self.manifest["dependent_fields"].append(
            {
                "field": "due_date",
                "depends_on": "terms",
                "terms": self.inv.terms,
                "expected": want,
                "observed": observed,
            }
        )

    async def add_item(self, row: int, item: Item) -> None:
        """Pick the item (Zoho then fills its Rate: second dependent field) and set the quantity.
        Rate/Quantity are looked up inside the row we tagged, not by Zoho's row numbers."""
        if row > 0:
            await self.click(targets.ADD_ROW)
        item_target, quantity_target = targets.item_targets(row)
        box = await self.resolve(item_target)
        tagged = await tag_row(box, row)
        await click(self.page, box, self.ctx.guard, item_target.key)
        await self.page.keyboard.type(item.name, delay=TYPE_DELAY_MS)
        await self.pick_option(item.name, f"item[{row}]")

        fields = await row_fields(self.page, row) if tagged else None
        rate_box, qty_box = fields or (
            await self.resolve(targets.rate_target(row)),
            await self.resolve(quantity_target),
        )
        await self.check_rate_filled(rate_box, row, item, "row-scoped" if fields else "resolver")
        await self.set_quantity(qty_box, item)

    async def check_rate_filled(self, rate_box: Locator, row: int, item: Item, located: str) -> None:
        rate = await self.poll_value(rate_box, lambda v: v.replace(",", "") not in EMPTY_RATES)
        if rate.replace(",", "") in EMPTY_RATES:
            raise RetryableError(f"Rate never filled in for {item.name!r} (row {row})")
        self.manifest["dependent_fields"].append(
            {
                "field": f"rate[{row}]",
                "depends_on": f"item[{row}]",
                "item": item.name,
                "observed": rate,
                "located": located,
            }
        )

    async def set_quantity(self, qty_box: Locator, item: Item) -> None:
        await qty_box.fill(str(item.quantity))
        await qty_box.press("Tab")
        got = (await qty_box.input_value()).replace(",", "")  # read back what Zoho kept
        try:
            matches = float(got) == float(item.quantity)
        except ValueError:
            matches = False
        if not matches:
            raise RetryableError(f"quantity for {item.name!r} reads {got!r}, expected {item.quantity}")

    async def poll_value(self, box: Locator, done: Callable[[str], bool]) -> str:
        value = await box.input_value()
        for _ in range(VALUE_POLLS):
            if done(value):
                break
            await self.page.wait_for_timeout(VALUE_POLL_MS)
            value = await box.input_value()
        return value

    async def save(self) -> None:
        await self.click(targets.SAVE_DRAFT)
        duplicate = self.page.get_by_text(re.compile(r"already (exists|been used)", re.I)).first

        async def saved(p: Page) -> bool:
            if await duplicate.is_visible():
                raise FinalError(f"Zoho says invoice number {self.inv.number} already exists")
            return bool(DETAIL_PAGE.search(p.url))

        try:
            await self.wait_for(saved, "the saved invoice page", SAVE_WAIT_S)
        except RetryableError as e:
            raise FinalError(f"save did not complete: {e}") from None
        self.ctx.record.visit("invoice_detail", self.page.url)

    async def download_pdf(self) -> Path:
        await self.click(targets.PDF_PRINT, FORM_WAIT_S)
        option = await self.resolve(targets.PDF_OPTION)
        async with self.page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
            await click(self.page, option, self.ctx.guard, targets.PDF_OPTION.key)
        download = await info.value
        self.ctx.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.ctx.out_dir / f"{self.inv.number}.pdf"
        await download.save_as(target)
        await self.page.keyboard.press("Escape")

        doc = self.ctx.record.add_document(target, "native download (PDF/Print > PDF)")
        if not doc["is_pdf"]:
            self.ctx.record.manifest["documents"].pop()
            raise RetryableError("the downloaded file is not a PDF")
        text = pdf_text(target)
        problems = verify_invoice_text(text, self.inv)
        doc.update(content_sha256=content_fingerprint(text), verified=not problems)
        if problems:
            raise FinalError("the PDF does not match the inputs: " + "; ".join(problems))
        return target
