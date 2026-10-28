"""Configuration and input errors exit with code 2 before any browser starts."""

import json

import pytest

from portal_runner import cli
from portal_runner.cli import run
from portal_runner.core.errors import ExitCode

INPUTS = {
    "portal": "zoho_invoice",
    "invoice": {"number": "A-1", "customer": "C", "invoice_date": "2026-10-29", "items": [{"name": "I"}]},
}


class NeverRun:
    def __init__(self, *_: object) -> None:
        raise AssertionError("a config error must stop before any browser starts")


@pytest.fixture
def inputs_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Runner", NeverRun)  # these tests must never reach a real portal
    monkeypatch.chdir(tmp_path)  # no .env here
    for name in ("ZOHO_EMAIL", "ZOHO_PASSWORD", "ZOHO_TOTP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(INPUTS))
    return path


def test_missing_env(inputs_file, capsys):
    assert run(["--inputs", str(inputs_file)]) == ExitCode.BAD_CONFIG
    assert "ZOHO_EMAIL" in capsys.readouterr().err


def test_invalid_totp_secret(inputs_file, monkeypatch, capsys):
    monkeypatch.setenv("ZOHO_EMAIL", "a@example.com")
    monkeypatch.setenv("ZOHO_PASSWORD", "x")
    monkeypatch.setenv("ZOHO_TOTP_SECRET", "not-base32!!")
    assert run(["--inputs", str(inputs_file)]) == ExitCode.BAD_CONFIG
    assert "base32" in capsys.readouterr().err


def test_unknown_portal(inputs_file, capsys):
    inputs_file.write_text(json.dumps({**INPUTS, "portal": "nope"}))
    assert run(["--inputs", str(inputs_file)]) == ExitCode.BAD_CONFIG
    assert "unknown portal" in capsys.readouterr().err


def test_missing_inputs_file(tmp_path, capsys):
    assert run(["--inputs", str(tmp_path / "missing.json")]) == ExitCode.BAD_CONFIG
    assert "not found" in capsys.readouterr().err
