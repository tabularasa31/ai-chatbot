release: alembic upgrade head
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 30
worker: python -m backend.worker
