# PyConduit developer tasks.
# On Windows, run these under Git Bash (the shell this repo assumes) or use the
# raw commands shown in the README.

PY ?= python
VENV ?= .venv
BIN := $(VENV)/bin
CONFIG ?= config.dev.yaml

.PHONY: help venv install run test lint fmt ejabberd-up ejabberd-down register clean

help:
	@echo "Targets:"
	@echo "  install       Create venv and install app + dev deps"
	@echo "  run           Run the PyConduit server (CONFIG=$(CONFIG))"
	@echo "  test          Run the test suite"
	@echo "  lint          Ruff lint"
	@echo "  fmt           Ruff format"
	@echo "  ejabberd-up   Start ejabberd (Docker) for development"
	@echo "  ejabberd-down Stop ejabberd"
	@echo "  register      Register the two sample accounts (alice, bob)"

venv:
	$(PY) -m venv $(VENV)

install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"

run:
	$(BIN)/python -m pyconduit $(CONFIG)

test:
	$(BIN)/python -m pytest -q

lint:
	$(BIN)/ruff check src tests

fmt:
	$(BIN)/ruff format src tests

ejabberd-up:
	docker compose up -d

ejabberd-down:
	docker compose down

# Register the sample accounts inside the running ejabberd container.
register:
	docker exec pyconduit-ejabberd ejabberdctl register alice example.com alicepass
	docker exec pyconduit-ejabberd ejabberdctl register bob   example.com bobpass

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__
