#!/usr/bin/env bash
set -euo pipefail

PG_USER="${PG_USER:-postgres}"
PG_PASSWORD="${PG_PASSWORD:-password}"
PG_DBNAME="${PG_DBNAME:-chatbot}"

echo "== Local test run =="

# 1) поднять БД
echo "== Starting docker compose db =="
docker compose up -d db

# 2) ждать готовности (pg_isready есть внутри postgres-контейнера)
echo "== Waiting for Postgres readiness =="
for i in {1..30}; do
  if docker compose exec -T db pg_isready -U "${PG_USER}" -d "${PG_DBNAME}" >/dev/null 2>&1; then
    echo "Postgres is ready (attempt $i)."
    break
  fi
  if (( i % 5 == 0 )); then
    echo "Postgres not ready yet (attempt $i/30)..."
  fi
  sleep 1
done

# 3) сначала обычные тесты (без pgvector / без multi-hop eval)
echo "== Running backend unit/integration tests (SQLite) =="
pytest -vv tests/ --ignore=tests/pgvector_tests --ignore=tests/eval/multi_hop --cov=backend --cov-report=term-missing --cov-report=xml

# 4) потом pgvector-тесты + multi-hop retrieval eval (нужен Docker Postgres)
echo "== Running pgvector integration tests (Docker Postgres) =="
PG_USER="${PG_USER}" PG_PASSWORD="${PG_PASSWORD}" PG_DBNAME="${PG_DBNAME}" pytest -vv -m pgvector tests/pgvector_tests/ tests/eval/multi_hop/ \
  --cov=backend --cov-append --cov-report=term-missing --cov-report=xml
