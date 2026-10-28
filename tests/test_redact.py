import json
import logging

import pytest

from portal_runner.core.record import RunRecord
from portal_runner.core.redact import RedactingFilter, Redactor

# Shapes that actually leaked from real Zoho pages during development.
REAL_LEAKS = [
    '"portal_name":"testing55500011122"',
    "cpage=https%3A%2F%2Finvoice.zoho.in%2Fapp%23%2Finvoices%2F9876543210000012345%3Ffilter",
    "cpage=%2finvoices%2f9876543210000012345%3f",
    'alt="Jane Doe"',
    '"user_name":"JANE DOE"',
]


def test_secrets_ids_and_emails_are_scrubbed():
    r = Redactor(["jane.doe@example.com", "hunter2pass", "JBSWY3DPEHPK3PXP"])
    r.add("123456")
    out = r.text(
        "user jane.doe@example.com pw hunter2pass code 123456 other x@example.com "
        "url https://invoice.zoho.in/app/55500011122#/invoices/9876543210000012345"
    )
    for leak in ["jane.doe", "hunter2pass", "123456", "x@example.com", "55500011122", "9876543210000012345"]:
        assert leak not in out
    assert "/app/[ORG]" in out


@pytest.mark.parametrize("raw", REAL_LEAKS)
def test_real_leak_shapes(raw):
    out = Redactor(["Jane Doe"]).text(raw).lower()
    for leak in ["55500011122", "9876543210000012345", "jane", "doe"]:
        assert leak not in out


def test_sha256_hashes_survive():
    digest = "feef6339b18b2a953004ea3552f87673c92289215f63043eafbba5795bec5244"
    assert Redactor().text(digest) == digest


def test_url_drops_query_strings():
    assert Redactor().url("https://a.zoho.in/signin?token=abc#/x?y=1") == "https://a.zoho.in/signin#/x"


def test_log_lines_are_redacted(caplog):
    logger = logging.getLogger("test.redact")
    caplog.handler.addFilter(RedactingFilter(Redactor(["hunter2pass"])))
    logger.warning("password was %s", "hunter2pass")
    assert "hunter2pass" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_manifest_values_are_redacted(tmp_path):
    record = RunRecord(tmp_path, "zoho_invoice", {"note": 'quote " and hunter2pass'}, Redactor(["hunter2pass"]))
    manifest = json.loads(record.finish("success").read_text())
    assert manifest["inputs"]["note"] == 'quote " and [REDACTED]'
    assert manifest["schema"] == 1
