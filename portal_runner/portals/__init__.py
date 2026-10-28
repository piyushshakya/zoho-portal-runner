"""Portal registry. Adding a portal = one package implementing ``Portal`` + one line here."""

from __future__ import annotations

from portal_runner.core.errors import InputError
from portal_runner.portals.base import Portal
from portal_runner.portals.zoho import ZohoInvoicePortal

PORTALS: dict[str, type[Portal]] = {ZohoInvoicePortal.name: ZohoInvoicePortal}


def get_portal(name: str) -> type[Portal]:
    try:
        return PORTALS[name]
    except KeyError:
        raise InputError(f"unknown portal {name!r} (available: {', '.join(PORTALS)})") from None
