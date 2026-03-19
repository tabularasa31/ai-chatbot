# Infrastructure: CI/CD — GitHub Actions

## Задача

Настроить автоматические проверки на каждый PR и push в `main`:
- Backend: pytest + ruff (linter)
- Frontend: eslint + build check

---

## Что создать

### 1. `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main, deploy]
  pull_request:
    branches: [main]

jobs:
  backend:
    name: Backend (pytest + ruff)
    runs-on: ubuntu-latest

    services:
      postgres:
        image: pgvector/pgvector:pg15
        env:
          POSTGRES_PASSWORD: password
          POSTGRES_DB: test_chatbot
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    env:
      DATABASE_URL: postgresql://postgres:password@localhost:5432/test_chatbot
      JWT_SECRET: test-secret-key-min-32-chars-long
      ENVIRONMENT: test
      ENCRYPTION_KEY: ${{ secrets.ENCRYPTION_KEY_TEST }}
      FRONTEND_URL: http://localhost:3000
      BREVO_API_KEY: test-key
      EMAIL_FROM: test@example.com

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          cd backend
          pip install -r requirements.txt
          pip install ruff

      - name: Run ruff (linter)
        run: |
          cd backend
          ruff check .

      - name: Run pytest
        run: |
          cd backend
          pytest --cov=backend --cov-report=term-missing -q

  frontend:
    name: Frontend (eslint + build)
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js 18
        uses: actions/setup-node@v4
        with:
          node-version: "18"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        run: |
          cd frontend
          npm ci

      - name: Run ESLint
        run: |
          cd frontend
          npm run lint

      - name: Build check
        run: |
          cd frontend
          npm run build
        env:
          NEXT_PUBLIC_API_URL: https://ai-chatbot-production-6531.up.railway.app
```

### 2. `backend/ruff.toml` (конфиг линтера)

```toml
line-length = 100
target-version = "py311"

[lint]
select = ["E", "F", "W", "I"]   # pycodestyle, pyflakes, isort
ignore = ["E501"]               # line too long — уже в line-length
```

### 3. Добавить `ruff` в `backend/requirements.txt`

```
ruff>=0.3.0
```

### 4. GitHub Secret для тестов

В репозитории добавить секрет:
- `ENCRYPTION_KEY_TEST` — любой валидный Fernet ключ для тестов
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

---

## Файлы для создания

1. `.github/workflows/ci.yml`
2. `backend/ruff.toml`
3. Обновить `backend/requirements.txt` — добавить `ruff>=0.3.0`

---

## Почему ruff, не flake8/black

- `ruff` заменяет flake8 + isort + pyupgrade — всё в одном
- В 10-100x быстрее
- Уже стандарт в современных Python проектах

---

## Ожидаемый результат

- Каждый PR показывает ✅/❌ на бейджах
- Merge заблокирован если тесты падают
- Автодеплой через `deploy` ветку остаётся как есть (Vercel)
