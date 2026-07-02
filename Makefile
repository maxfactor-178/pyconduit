# PyConduit developer tasks.
# On Windows, run these under Git Bash (the shell this repo assumes) or use the
# raw commands shown in the README.

PY ?= python
VENV ?= .venv
BIN := $(VENV)/bin
CONFIG ?= config.dev.yaml

.PHONY: help venv install run test lint fmt certs ejabberd-up ejabberd-down register rooms clean

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
	@echo "  rooms         Create sample discoverable MUC rooms"

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

# Generate a self-signed dev certificate for ejabberd's STARTTLS.
certs:
	mkdir -p certs
	MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
	  -keyout certs/key.pem -out certs/cert.pem -subj "/CN=example.com" \
	  -addext "subjectAltName=DNS:example.com,DNS:conference.example.com"
	cat certs/cert.pem certs/key.pem > certs/server.pem

ejabberd-up: certs
	docker compose up -d

ejabberd-down:
	docker compose down

# Register the sample accounts inside the running ejabberd container.
register:
	docker exec pyconduit-ejabberd ejabberdctl register alice example.com alicepass
	docker exec pyconduit-ejabberd ejabberdctl register bob   example.com bobpass

# Create a few persistent, publicly-discoverable MUC rooms so Discover has content.
rooms:
	@for r in general random support dev-team; do \
	  docker exec pyconduit-ejabberd ejabberdctl create_room $$r conference.example.com example.com || true; \
	  docker exec pyconduit-ejabberd ejabberdctl change_room_option $$r conference.example.com persistent true; \
	  docker exec pyconduit-ejabberd ejabberdctl change_room_option $$r conference.example.com public true; \
	done
	@echo "Created rooms on conference.example.com:"
	@docker exec pyconduit-ejabberd ejabberdctl muc_online_rooms conference.example.com

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__
