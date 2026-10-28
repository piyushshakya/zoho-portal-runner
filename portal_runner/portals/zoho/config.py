"""Zoho settings from the environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from portal_runner.core.errors import ConfigError

REQUIRED = ("ZOHO_EMAIL", "ZOHO_PASSWORD", "ZOHO_TOTP_SECRET")


@dataclass(frozen=True)
class ZohoConfig:
    email: str = field(repr=False)
    password: str = field(repr=False)
    totp_secret: str = field(repr=False)
    data_centre: str = "in"
    redact_names: tuple[str, ...] = field(default=(), repr=False)

    @property
    def app_url(self) -> str:
        return f"https://invoice.zoho.{self.data_centre}/app"


def load_config() -> ZohoConfig:
    # Explicit path: by default python-dotenv searches upward from *this file*, not the working directory.
    load_dotenv(Path.cwd() / ".env")
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        raise ConfigError(f"missing in .env: {', '.join(missing)} (see .env.example)")
    return ZohoConfig(
        email=os.environ["ZOHO_EMAIL"],
        password=os.environ["ZOHO_PASSWORD"],
        totp_secret=os.environ["ZOHO_TOTP_SECRET"],
        data_centre=os.getenv("ZOHO_DC", "in"),
        redact_names=tuple(n.strip() for n in os.getenv("REDACT_NAMES", "").split(",") if n.strip()),
    )
