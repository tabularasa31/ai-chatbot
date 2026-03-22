# Infrastructure: CI/CD GitHub Actions — reference (FI-026, implemented)

Справка по текущему CI в репозитории. Не использовать старые инструкции с `cd backend && pytest` или обязательным Postgres в CI — тесты в [`tests/`](../tests/) на **SQLite**.

---

## Workflow

**Файл:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

**Триггеры:** `push` и `pull_request` на ветки **`main`** и **`deploy`**.

| Job | Что делает |
|-----|------------|
| **Backend (pytest + ruff)** | Python 3.11, из корня репо: `pip install -r backend/requirements.txt`, `ruff check backend`, `pytest tests/ -q --cov=backend --cov-report=term-missing` |
| **Frontend (eslint + build)** | Node 20, в `frontend/`: `npm ci`, `npm run lint`, `npm run build` с `NEXT_PUBLIC_API_URL=https://ci.invalid` |

Переменные для тестов заданы в job (секреты GitHub **не** нужны для CI).

---

## Конфиг и зависимости

- [`backend/ruff.toml`](../backend/ruff.toml) — правила E/F/W; `extend-exclude` для `migrations/`; per-file `E402` для `main.py` и `chat/service.py`.
- [`backend/requirements.txt`](../backend/requirements.txt) — **`ruff>=0.3.0`**, **`pgvector>=0.2.0`** (импорт `backend.models` в тестах).

---

## Локально (как в CI)

Из корня репозитория (Python 3.11+):

```bash
pip install -r backend/requirements.txt
ruff check backend
pytest tests/ -q --cov=backend --cov-report=term-missing
```

Frontend:

```bash
cd frontend && npm ci && npm run lint && NEXT_PUBLIC_API_URL=https://ci.invalid npm run build
```

---

## Прод: `deploy`

Рекомендуется PR **`main` → `deploy`** после зелёного CI. Опционально — **branch ruleset** на `deploy` (обязательный PR + required checks: `Backend (pytest + ruff)`, `Frontend (eslint + build)`).

---

## Шаблон описания PR (English)

```markdown
## Summary
GitHub Actions CI: backend (ruff + pytest + coverage) and frontend (eslint + next build) on PR/push to `main` and `deploy`.

## Changes
- `.github/workflows/ci.yml`
- `backend/ruff.toml`, `backend/requirements.txt` (ruff + pgvector)

## Testing
- [ ] CI green on this PR
```
