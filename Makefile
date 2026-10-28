.PHONY: setup test lint format check-secrets run offline serve

PY := .venv/bin/python

setup:          ## create .venv, install everything, prepare .env
	./setup.sh

test:           ## offline test suite (no Zoho account needed)
	$(PY) -m pytest -q

lint:           ## formatting, lint and type checks
	$(PY) -m ruff format --check .
	$(PY) -m ruff check .
	$(PY) -m mypy portal_runner tools

format:         ## apply the standard formatting
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

check-secrets:  ## make sure no secret is about to be committed
	$(PY) tools/check_secrets.py

run:            ## run with inputs.json
	$(PY) -m portal_runner

offline:        ## resolver on the saved Zoho pages, network off
	$(PY) -m portal_runner offline tests/fixtures/*.html

serve:          ## job API + UI on http://127.0.0.1:8000
	$(PY) -m portal_runner serve --workers 2
