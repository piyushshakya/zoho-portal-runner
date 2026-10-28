#!/usr/bin/env bash
# Installed by setup.sh as .git/hooks/pre-commit
exec .venv/bin/python tools/check_secrets.py
