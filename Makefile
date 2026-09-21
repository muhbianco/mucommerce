API_DIR := apps/api-commerce
WEB_DIR := apps/web
VENV := $(API_DIR)/.venv

ifeq ($(OS),Windows_NT)
PY := $(VENV)/Scripts/python.exe
PIP := $(VENV)/Scripts/pip.exe
ALEMBIC := $(VENV)/Scripts/alembic.exe
else
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
ALEMBIC := $(VENV)/bin/alembic
endif

.PHONY: dev-infra dev-infra-down api-install api-migrate api-run api-worker api-beat api-test api-lint web-install web-dev web-build web-test web-e2e

dev-infra:
	docker compose -f compose.dev.yml up -d

dev-infra-down:
	docker compose -f compose.dev.yml down

api-install:
	python -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r $(API_DIR)/requirements-dev.txt

api-migrate:
	cd $(API_DIR) && $(CURDIR)/$(PY) -m app.cli db ensure && $(CURDIR)/$(ALEMBIC) upgrade head

api-run:
	cd $(API_DIR) && $(CURDIR)/$(PY) -m uvicorn app.main:app --reload --port 8000

api-worker:
	cd $(API_DIR) && $(CURDIR)/$(PY) -m celery -A app.workers.celery_app:celery_app worker -l info -Q commerce.default,commerce.outbox,commerce.payments,commerce.notifications,commerce.provisioning,commerce.media

api-beat:
	cd $(API_DIR) && $(CURDIR)/$(PY) -m celery -A app.workers.celery_app:celery_app beat -l info

api-lint:
	cd $(API_DIR) && $(CURDIR)/$(PY) -m ruff check . && $(CURDIR)/$(PY) -m ruff format --check . && $(CURDIR)/$(PY) -m mypy app

api-test: api-lint
	cd $(API_DIR) && $(CURDIR)/$(PY) -m pytest -q

web-install:
	cd $(WEB_DIR) && pnpm install

web-dev:
	cd $(WEB_DIR) && pnpm dev

web-build:
	cd $(WEB_DIR) && pnpm build

web-test:
	cd $(WEB_DIR) && pnpm lint && pnpm typecheck && pnpm test

# Playwright against web + api-commerce (SQLite) + fake MuhBianco login; no Docker. Locally:
#   E2E_BROWSER_CHANNEL=msedge make web-e2e   (uses the installed Edge, no browser download)
web-e2e:
	cd $(WEB_DIR) && E2E_PYTHON=$(CURDIR)/$(PY) pnpm e2e
