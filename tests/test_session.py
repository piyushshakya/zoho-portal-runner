import json
import stat
import time

from portal_runner.core.session import SessionStore
from portal_runner.portals.zoho import is_app_url, is_login_url


def store_with(tmp_path, content: str) -> SessionStore:
    path = tmp_path / "state.json"
    path.write_text(content)
    return SessionStore(path, cookie_domain="zoho")


def cookies(*expires: float) -> str:
    return json.dumps(
        {"cookies": [{"name": f"c{i}", "domain": ".zoho.in", "expires": e} for i, e in enumerate(expires)]}
    )


def test_missing_file(tmp_path):
    assert not SessionStore(tmp_path / "none.json", "zoho").preflight().alive


def test_corrupt_file(tmp_path):
    verdict = store_with(tmp_path, "garbage").preflight()
    assert not verdict.alive
    assert "corrupt" in verdict.reason


def test_all_cookies_expired(tmp_path):
    verdict = store_with(tmp_path, cookies(time.time() - 60)).preflight()
    assert not verdict.alive
    assert "expired" in verdict.reason


def test_valid_cookies_including_session_cookies(tmp_path):
    assert store_with(tmp_path, cookies(time.time() + 3600, -1)).preflight().alive


def test_discard_moves_the_file_aside(tmp_path):
    store = store_with(tmp_path, "garbage")
    store.discard("test")
    assert not store.path.exists()
    assert (tmp_path / "state.dead.json").exists()


async def test_saved_session_is_private(tmp_path, browser):
    context = await browser.new_context()
    store = SessionStore(tmp_path / "s" / "state.json", "zoho")
    await store.save(context)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_host_checks_ignore_the_query_string():
    login = "https://accounts.zoho.in/signin?serviceurl=https%3A%2F%2Finvoice.zoho.in%2Fapp"
    assert is_login_url(login)
    assert not is_app_url(login)
    assert is_app_url("https://invoice.zoho.in/app#/home/dashboard")
