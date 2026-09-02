PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
DB     ?= caic.db

.PHONY: help setup test lint seed snapshot smoke digests fixtures clean

help:
	@echo "make setup     create the venv and install with dev extras"
	@echo "make test      run the offline test suite"
	@echo "make lint      ruff check"
	@echo "make seed      populate $(DB) with the current season (hits the network)"
	@echo "make snapshot  archive today's forecast (run daily via cron)"
	@echo "make smoke     drive the MCP server over stdio as a client would"
	@echo "make digests   render human-readable Markdown digests"
	@echo "make fixtures  refresh recorded API payloads used by the tests"

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

setup: $(BIN)/python
	$(BIN)/pip install --upgrade pip -q
	$(BIN)/pip install -e ".[dev]" -q
	@echo "Ready. Next: make seed"

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check avalanche tests scripts

seed:
	CAIC_DB=$(DB) $(BIN)/python scripts/seed.py --db $(DB)

snapshot:
	CAIC_DB=$(DB) $(BIN)/avalanche --db $(DB) snapshot

smoke:
	CAIC_DB=$(DB) $(BIN)/python scripts/smoke_test.py

digests:
	$(BIN)/avalanche --db $(DB) digests --out digests

fixtures:
	$(BIN)/python scripts/refresh_fixtures.py

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__ *.egg-info
