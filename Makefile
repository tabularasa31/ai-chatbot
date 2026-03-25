SHELL := /bin/bash
COMPOSE := docker compose

# Default creds for local pgvector container (see docker-compose.yml)
PG_USER ?= postgres
PG_PASSWORD ?= password
PG_DBNAME ?= chatbot

.PHONY: db-up db-down db-recreate db-ready db-logs test test-sqlite test-pgvector coverage clean smoke auth-reset escalation rag-edge pgvector-only
.PHONY: coverage-all

db-up:
	$(COMPOSE) up -d db

db-down:
	$(COMPOSE) down

db-recreate:
	$(COMPOSE) down -v
	$(COMPOSE) up -d db

db-ready:
	@echo "Waiting for Postgres readiness..."
	@for i in {1..30}; do \
		if $(COMPOSE) exec -T db pg_isready -U "$(PG_USER)" -d "$(PG_DBNAME)" >/dev/null 2>&1; then \
			echo "Postgres is ready"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Postgres is not ready after timeout" >&2; \
	exit 1

db-logs:
	$(COMPOSE) logs -f db

# Runs the full local test workflow:
# 1) brings up docker db
# 2) runs sqlite tests (without pgvector) with coverage
# 3) runs pgvector tests (with real PG)
test:
	./scripts/run-tests-local.sh

test-sqlite:
	PYTHONPATH=. pytest tests/ --ignore=tests/pgvector_tests --cov=backend --cov-report=term-missing

test-pgvector: db-up db-ready
	PG_USER="$(PG_USER)" PG_PASSWORD="$(PG_PASSWORD)" PYTHONPATH=. pytest -m pgvector tests/pgvector_tests/

# Coverage snapshot for backend (sqlite path). If you want coverage that includes pgvector too,
# run pgvector tests with pytest-cov and --cov-append.
coverage:
	PYTHONPATH=. pytest --cov=backend --cov-report=term-missing --cov-report=xml

# Coverage for the full suite (SQLite tests + pgvector tests).
# Requires Docker Postgres to be up for the pgvector stage.
coverage-all: db-up db-ready
	rm -f .coverage coverage.xml
	PYTHONPATH=. pytest tests/ --ignore=tests/pgvector_tests --cov=backend --cov-report=xml
	PG_USER="$(PG_USER)" PG_PASSWORD="$(PG_PASSWORD)" PYTHONPATH=. pytest -m pgvector tests/pgvector_tests/ \
	  --cov=backend --cov-append --cov-report=term-missing --cov-report=xml

clean:
	rm -f .coverage coverage.xml

smoke:
	PYTHONPATH=. pytest -q \
		tests/test_chat.py \
		tests/test_escalation.py \
		tests/test_auth.py \
		tests/test_auth_email_verification.py \
		tests/test_verification_enforcement.py \
		-k "escalat or verify or forgot_password or reset_password"

auth-reset:
	PYTHONPATH=. pytest -q tests/test_auth.py -k "forgot_password or reset_password"

escalation:
	PYTHONPATH=. pytest -q \
		tests/test_chat.py \
		tests/test_escalation.py \
		-k "awaiting_email or followup or already_closed or manual_escalate or perform_manual_escalation"

rag-edge:
	PYTHONPATH=. pytest -q \
		tests/test_search.py \
		tests/test_chat.py \
		-k "openai_unavailable or malformed or wrong_dimension or low_vector"

pgvector-only: db-up db-ready
	PG_USER="$(PG_USER)" PG_PASSWORD="$(PG_PASSWORD)" PYTHONPATH=. pytest -q -m pgvector tests/pgvector_tests/

