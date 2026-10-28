"""Route all browser traffic through a proxy: PROXY_URL=http://user:pass@host:port"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from playwright.async_api import ProxySettings

from portal_runner.core.errors import ConfigError

SCHEMES = ("http", "https", "socks5")


def parse_proxy(url: str) -> ProxySettings:
    parts = urlparse(url.strip())
    if parts.scheme not in SCHEMES or not parts.hostname:
        raise ConfigError(f"proxy must look like http://host:port (schemes: {', '.join(SCHEMES)})")
    server = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    settings: ProxySettings = {"server": server}
    if parts.username:
        settings["username"] = unquote(parts.username)
        settings["password"] = unquote(parts.password or "")
    return settings
