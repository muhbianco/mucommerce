API_DIR := apps/api-commerce
WEB_DIR := apps/web
VENV := $(API_DIR)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: dev-infra dev-infra-down api-install api-migrate api-run api-worker api-beat api-test api-lint web-install web-dev web-build web-test

dev-infra:
	docker compose -f compose.dev.yml up -d

dev-infra-down:
	docker compose -f compose.dev.yml down

api-install:
	python -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r $(API_DIR)/requirements-dev.txt

api-migrate:
	cd $(API_DIR) && $(abspath $(PY)) -m app.cli db ensure && $(abspath $(VENV))/bin/alembic upgrade head

api-run:
	cd $(API_DIR) && $(abspath $(VENV))/bin/uvicorn app.main:app --reload --port 8000

api-worker:
	cd $(API_DIR) && $(abspath $(VENV))/bin/celery -A app.workers.celery_app:celery_app worker -l info -Q commerce.default,commerce.outbox,commerce.payments,commerce.notifications,commerce.provisioning,commerce.media

api-beat:
	cd $(API_DIR) && $(abspath $(VENV))/bin/celery -A app.workers.celery_app:celery_app beat -l info

api-lint:
	cd $(API_DIR) && $(abspath $(VENV))/bin/ruff check . && $(abspath $(VENV))/bin/ruff format --check . && $(abspath $(VENV))/bin/mypy app

api-test: api-lint
	cd $(API_DIR) && $(abspath $(VENV))/bin/pytest -q

web-install:
	cd $(WEB_DIR) && pnpm install

web-dev:
	cd $(WEB_DIR) && pnpm dev

web-build:
	cd $(WEB_DIR) && pnpm build

web-test:
	cd $(WEB_DIR) && pnpm lint && pnpm typecheck && pnpm test
