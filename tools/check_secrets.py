"""Refuse to commit secrets. Run by the git pre-commit hook and by `make check-secrets`.

Scans every file git would commit for the values in .env (email, password, TOTP secret,
REDACT_NAMES) and for any email address outside example.com. Prints file:line and the
kind of secret, never the secret itself. Exit code 1 if anything is found.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

SECRET_KEYS = ("ZOHO_EMAIL", "ZOHO_PASSWORD", "ZOHO_TOTP_SECRET")
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
ALLOWED_EMAIL_DOMAINS = ("example.com",)
MIN_LEN = 4


def tracked_files() -> list[Path]:
    out = subprocess.run(  # noqa: S603 -- fixed command, no user input
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.splitlines() if Path(p).is_file()]


def secrets_from_env(path: Path = Path(".env")) -> dict[str, str]:
    env = dotenv_values(path) if path.exists() else {}
    found = {k: v.strip() for k in SECRET_KEYS if (v := env.get(k))}
    if email := found.get("ZOHO_EMAIL"):
        found["ZOHO_EMAIL (user part)"] = email.split("@")[0]
    for i, name in enumerate((env.get("REDACT_NAMES") or "").split(",")):
        if name.strip():
            found[f"REDACT_NAMES[{i}]"] = name.strip()
    return {k: v for k, v in found.items() if len(v) >= MIN_LEN}


def scan(files: list[Path], secrets: dict[str, str]) -> list[str]:
    patterns = {kind: re.compile(re.escape(value), re.IGNORECASE) for kind, value in secrets.items()}
    problems = []
    for file in files:
        if file.name == ".env":
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue  # binary files (images, PDFs) are kept out by .gitignore
        for n, line in enumerate(lines, 1):
            problems += [f"{file}:{n}: {kind}" for kind, p in patterns.items() if p.search(line)]
            problems += [
                f"{file}:{n}: email address"
                for m in EMAIL.finditer(line)
                if not m.group(1).lower().endswith(ALLOWED_EMAIL_DOMAINS)
            ]
    return problems


def main() -> int:
    try:
        files = tracked_files()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("check-secrets: not a git repository yet -- run `git init` first", file=sys.stderr)
        return 2
    problems = scan(files, secrets_from_env())
    for problem in problems:
        print(f"secret found: {problem}", file=sys.stderr)
    if problems:
        print("Commit refused. Remove the values above (or redact them) and try again.", file=sys.stderr)
        return 1
    print("check-secrets: no secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
