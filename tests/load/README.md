# Load tests (k6)

Sustained-load scenarios for the chat pipeline. Used to verify the
async/sync DB boundary in `backend/chat/` holds under real concurrency.

## Prerequisites

- [k6](https://k6.io/docs/get-started/installation/) (`brew install k6`)
- A tenant API key on the target environment (staging/Railway)

## Run

```bash
BASE_URL=https://ai-chatbot-production-6531.up.railway.app \
API_KEY=<tenant-api-key> \
k6 run tests/load/chat_endpoint.js
```

Defaults: 15 RPS for 2 minutes. Override via `RPS`, `DURATION`,
`PREALLOC`, `MAX_VUS`.

## What to watch

While the run is in progress, tail Railway logs for the backend service:

```bash
railway logs --service backend | grep -iE "MissingGreenlet|NoActiveSqlalchemyContext|greenlet_spawn"
```

**Pass criterion:** zero `MissingGreenlet` / `NoActiveSqlalchemyContext`
errors during the run. k6's own thresholds (`http_req_failed<2%`,
`p95<8s`) must also pass.

If errors appear, the offending sync DB call is being made outside
`run_sync(db, ...)`. Cross-reference the stack trace with
`backend/core/db.py::run_sync` call sites.
