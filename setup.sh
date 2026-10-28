#!/usr/bin/env bash
# One-command setup:  ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is required (found: $("$PYTHON" --version 2>&1))." >&2
  exit 1
fi

echo "1/4 Creating the virtual environment (.venv)"
[ -d .venv ] || "$PYTHON" -m venv .venv

echo "2/4 Installing the project and its pinned dependencies"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[dev]"

echo "3/4 Installing the Chromium browser for Playwright"
.venv/bin/python -m playwright install chromium

echo "4/4 Preparing local files"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    created .env -- open it and fill in your Zoho details"
fi
chmod 600 .env
mkdir -p state && chmod 700 state
if [ -d .git ]; then
  ln -sf ../../tools/pre-commit.sh .git/hooks/pre-commit
  echo "    installed the git pre-commit secret check"
fi

cat <<'DONE'

Setup complete. Next:
  1. Fill in .env              (see README: "Set up your Zoho account")
  2. source .venv/bin/activate
  3. make test                 (offline tests, no account needed)
  4. python -m portal_runner --headed
DONE
