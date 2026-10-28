"""The ``invoice`` section of inputs.json, and Zoho's date rules."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from portal_runner.core.errors import InputError
from portal_runner.core.inputs import require

INVOICE_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,30}")  # also used as the PDF file name


@dataclass(frozen=True)
class Item:
    name: str
    quantity: float = 1


@dataclass(frozen=True)
class InvoiceInput:
    number: str
    customer: str
    invoice_date: date
    terms: str
    items: tuple[Item, ...]
    notes: str | None = None


def _item(raw: Any, i: int) -> Item:
    if not isinstance(raw, dict):
        raise InputError(f"invoice.items[{i}] must be an object")
    quantity = raw.get("quantity", 1)
    if isinstance(quantity, bool) or not isinstance(quantity, int | float) or quantity <= 0:
        raise InputError(f"invoice.items[{i}].quantity must be a positive number")
    return Item(name=str(require(raw, "name", f"invoice.items[{i}]")), quantity=quantity)


def parse_invoice(data: dict[str, Any]) -> InvoiceInput:
    inv = require(data, "invoice", "inputs")
    if not isinstance(inv, dict):
        raise InputError("inputs.invoice must be an object")
    number = str(require(inv, "number", "invoice")).strip()
    if not INVOICE_NUMBER.fullmatch(number):
        raise InputError(f"invoice.number {number!r}: use letters, digits, - or _ (max 31 characters)")
    try:
        invoice_date = date.fromisoformat(str(require(inv, "invoice_date", "invoice")))
    except ValueError:
        raise InputError("invoice.invoice_date must be YYYY-MM-DD") from None
    items = require(inv, "items", "invoice")
    if not isinstance(items, list):
        raise InputError("invoice.items must be a list")
    return InvoiceInput(
        number=number,
        customer=str(require(inv, "customer", "invoice")),
        invoice_date=invoice_date,
        terms=str(inv.get("terms") or "Due on Receipt"),
        items=tuple(_item(raw, i) for i, raw in enumerate(items)),
        notes=inv.get("notes"),
    )


def zoho_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def expected_due_date(invoice_date: date, terms: str) -> date | None:
    """The Due Date Zoho should compute for these terms; None for custom terms."""
    t = terms.strip().lower()
    if t == "due on receipt":
        return invoice_date
    if m := re.fullmatch(r"net\s+(\d+)", t):
        return invoice_date + timedelta(days=int(m.group(1)))
    if t == "due end of the month":
        return _month_end(invoice_date.year, invoice_date.month)
    if t == "due end of next month":
        nxt = invoice_date.replace(day=1) + timedelta(days=32)
        return _month_end(nxt.year, nxt.month)
    return None
