"""Zoho Invoice: TOTP login, invoice form, PDF download."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from portal_runner.core.codes import TotpProvider
from portal_runner.core.popups import Popup
from portal_runner.core.session import SessionRules, host
from portal_runner.portals.base import FlowContext, Portal
from portal_runner.portals.zoho import targets
from portal_runner.portals.zoho.config import ZohoConfig, load_config
from portal_runner.portals.zoho.inputs import InvoiceInput, parse_invoice
from portal_runner.portals.zoho.invoice import InvoiceFlow
from portal_runner.portals.zoho.login import full_login
from portal_runner.portals.zoho.popups import ZOHO_POPUPS


def is_app_url(url: str) -> bool:
    return host(url).startswith("invoice.zoho.")


def is_login_url(url: str) -> bool:
    return host(url).startswith("accounts.zoho.")


class ZohoInvoicePortal(Portal):
    name = "zoho_invoice"
    offline_targets = targets.INVOICE_FORM

    def __init__(self, config: ZohoConfig, invoice: InvoiceInput) -> None:
        self.config = config
        self.invoice = invoice
        self.codes = TotpProvider(config.totp_secret)  # validates the secret early

    @classmethod
    def from_env(cls, inputs: dict[str, Any]) -> ZohoInvoicePortal:
        return cls(load_config(), parse_invoice(inputs))

    @property
    def secrets(self) -> Sequence[str]:
        c = self.config
        return (c.email, c.password, c.totp_secret, *c.redact_names)

    @property
    def session_rules(self) -> SessionRules:
        return SessionRules(
            app_url=self.config.app_url,
            is_app_url=is_app_url,
            is_login_url=is_login_url,
            logged_in_marker=targets.NAV_INVOICES,
            cookie_domain="zoho",
        )

    @property
    def popups(self) -> Sequence[Popup]:
        return ZOHO_POPUPS

    def describe(self) -> str:
        return f"invoice {self.invoice.number} for {self.invoice.customer}"

    async def login(self, ctx: FlowContext) -> None:
        await full_login(ctx, self.config, self.codes, is_app_url)

    async def run(self, ctx: FlowContext) -> Path:
        return await InvoiceFlow(ctx, self.invoice).run()
