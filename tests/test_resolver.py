"""Resolver on the real (sanitized) Zoho pages, mutated copies, and small edge-case pages."""

import logging
from pathlib import Path

import pytest

from portal_runner.core.mutate import mutate
from portal_runner.core.offline import resolve_all
from portal_runner.core.resolver import STRATEGY_ORDER, ResolveError, Resolver, Target
from portal_runner.portals.zoho.targets import INVOICE_FORM

FIXTURES = Path(__file__).parent / "fixtures"
REAL_PAGES = ["invoice_new.html", "invoice_customer_selected.html", "invoice_terms_open.html"]
EVERYTHING = {"classes", "ids", "reorder", "wrap", "iframe"}
MUTATIONS = {
    "renamed_classes": {"classes"},
    "renamed_ids": {"ids"},
    "reordered_siblings": {"reorder"},
    "added_wrappers": {"wrap"},
    "inside_iframe": {"iframe"},
    "all_at_once": EVERYTHING,
    "no_aria": {"strip_aria"},  # beyond the brief: forces the fallback strategies
    "no_aria_all_at_once": EVERYTHING | {"strip_aria"},
}
_cache: dict[str, str] = {}


async def variant(name: str) -> str:
    if name not in _cache:
        _cache[name] = await mutate((FIXTURES / "invoice_new.html").read_text(encoding="utf-8"), MUTATIONS[name])
    return _cache[name]


def missing(rows: list[dict]) -> list[str]:
    return [r["key"] for r in rows if not r["found"]]


@pytest.mark.parametrize("fixture", REAL_PAGES)
async def test_real_pages_resolve_by_role(offline_page, fixture):
    rows = await resolve_all(await offline_page((FIXTURES / fixture).read_text(encoding="utf-8")), INVOICE_FORM)
    assert missing(rows) == []
    assert {r["strategy"] for r in rows} == {"role"}


@pytest.mark.parametrize("mutation", list(MUTATIONS))
async def test_every_field_survives_mutation(offline_page, mutation):
    rows = await resolve_all(await offline_page(await variant(mutation)), INVOICE_FORM)
    assert missing(rows) == []


async def test_iframe_results_report_the_frame(offline_page):
    rows = await resolve_all(await offline_page(await variant("inside_iframe")), INVOICE_FORM)
    assert all(r["frame"].startswith("iframe") for r in rows)


async def test_without_aria_the_fallback_chain_is_used(offline_page):
    rows = await resolve_all(await offline_page(await variant("no_aria")), INVOICE_FORM)
    assert "near_text" in {r["strategy"] for r in rows}


async def test_hidden_duplicates_are_ignored(offline_page):
    page = await offline_page('<button style="display:none">Verify</button><button>Verify</button>')
    found = await Resolver(page, default_timeout=0.3).resolve(Target("verify", role="button", name="Verify"))
    assert found.strategy == "role"
    assert await found.locator.is_visible()


async def test_ambiguous_text_falls_through_instead_of_guessing(offline_page):
    page = await offline_page('<label>Amount</label><input><label>Amount</label><input class="main-amount">')
    target = Target("amount", label="Amount", near_text="Amount", css=("input.main-amount",))
    assert (await Resolver(page, default_timeout=0.3).resolve(target)).strategy == "css"


async def test_failure_records_every_strategy(offline_page):
    page = await offline_page("<input>")
    target = Target(
        "ghost", role="textbox", name="Ghost", label="Ghost", near_text="Ghost", structure=("input.x", 0), css=("#x",)
    )
    with pytest.raises(ResolveError) as e:
        await Resolver(page, default_timeout=0.3).resolve(target)
    assert {a.strategy for a in e.value.attempts} == set(STRATEGY_ORDER)


async def test_broken_target_is_unfindable(offline_page):
    page = await offline_page("<label for=t>Terms</label><input id=t>")
    target = Target("invoice.terms", role="textbox", name="Terms", label="Terms")
    assert (await Resolver(page, default_timeout=0.3).resolve(target)).strategy == "role"
    with pytest.raises(ResolveError):
        await Resolver(page, default_timeout=0.3, broken=frozenset({"invoice.terms"})).resolve(target)


async def test_winner_is_logged(offline_page, caplog):
    caplog.set_level(logging.INFO, logger="portal_runner.core.resolver")
    page = await offline_page("<label for=q>Qty</label><input id=q>")
    await Resolver(page, default_timeout=0.3).resolve(Target("qty", role="textbox", name="Qty"))
    assert "qty" in caplog.text
    assert "role" in caplog.text
