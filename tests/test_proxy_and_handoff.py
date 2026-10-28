import time

import pytest

from portal_runner.core.errors import ConfigError, FinalError
from portal_runner.core.handoff import wait_for_human
from portal_runner.core.proxy import parse_proxy
from portal_runner.portals.zoho import ZohoInvoicePortal
from portal_runner.portals.zoho.config import ZohoConfig
from portal_runner.portals.zoho.inputs import parse_invoice
from portal_runner.runner import Runner, RunOptions

CAPTCHA_PAGE = """
<input placeholder="Enter CAPTCHA" id="c">
<script>setTimeout(() => document.getElementById('c').remove(), %d)</script>
"""


def test_proxy_with_credentials():
    assert parse_proxy("http://user:p%40ss@proxy.example.com:8080") == {
        "server": "http://proxy.example.com:8080",
        "username": "user",
        "password": "p@ss",
    }


def test_proxy_without_credentials():
    assert parse_proxy("socks5://10.0.0.1:1080") == {"server": "socks5://10.0.0.1:1080"}


@pytest.mark.parametrize("bad", ["proxy.example.com:8080", "ftp://x:1", "http://"])
def test_bad_proxy_is_a_config_error(bad):
    with pytest.raises(ConfigError):
        parse_proxy(bad)


def test_proxy_password_is_redacted(tmp_path):
    config = ZohoConfig(email="jane.doe@example.com", password="pw-1234", totp_secret="JBSWY3DPEHPK3PXP")
    invoice = parse_invoice(
        {"invoice": {"number": "A-1", "customer": "C", "invoice_date": "2026-10-29", "items": [{"name": "I"}]}}
    )
    options = RunOptions(runs_dir=tmp_path, proxy="http://u:proxy-secret@h.example.com:3128")
    runner = Runner(ZohoInvoicePortal(config, invoice), {}, options)
    assert "proxy-secret" not in runner.redactor.text("connect via u:proxy-secret@h.example.com")


async def test_handoff_continues_once_the_person_solves_it(offline_page):
    page = await offline_page(CAPTCHA_PAGE % 1500)
    captcha = page.get_by_placeholder("Enter CAPTCHA")
    started = time.monotonic()
    waited = await wait_for_human(page, captcha.is_visible, "test CAPTCHA", timeout_s=10)
    assert 1 <= waited < 5
    assert time.monotonic() - started < 6


async def test_handoff_times_out_as_a_final_error(offline_page):
    page = await offline_page(CAPTCHA_PAGE % 60_000)
    with pytest.raises(FinalError, match="not solved"):
        await wait_for_human(page, page.get_by_placeholder("Enter CAPTCHA").is_visible, "test CAPTCHA", timeout_s=1)
