# Frontend

Next.js 14 frontend for the Chat9 dashboard, marketing site, and embedded widget UI.

## What lives here

- marketing / landing pages
- auth flow
- app dashboard pages (`/knowledge`, `/settings`, logs, review, escalations, admin views)
- iframe widget UI under `/widget`
- lightweight BFF-style proxy routes for widget actions

## Local development

From the `frontend/` directory:

```bash
npm install
cp .env.local.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Required env:

- `NEXT_PUBLIC_API_URL` — backend base URL

Useful optional env:

- `NEXT_PUBLIC_POSTHOG_KEY` / `NEXT_PUBLIC_POSTHOG_HOST` — product analytics, when enabled.

Backend model selection is configured in the API service, not in the frontend:

- `HUMAN_REQUEST_MODEL` — human-request guard classifier, default `gpt-4o-mini`
- `RELEVANCE_GUARD_MODEL` — relevance guard classifier, default `gpt-4o-mini`
- `VALIDATION_MODEL` — answer validation classifier, default `gpt-4o-mini`
- `CHAT_MODEL` — main answer generation model, default `gpt-5-mini`

For rollback, set the specific backend env var to `gpt-4.1-mini`.

## Scripts

```bash
npm run dev
npm run build
npm run start
npm run lint
```

## Key app areas

- `app/(marketing)` — public landing pages
- `app/(auth)` — sign-in / auth flow
- `app/(app)` — authenticated dashboard pages
- `app/widget` — embedded widget iframe experience
- `components/` — shared UI and widget components

## Deployment

- hosted on Vercel
- production releases usually follow the repo branch workflow documented in the root project docs
- CI runs `next build` and `next lint` from GitHub Actions

For the full project runbook, API overview, and deployment notes, see the root [`README.md`](../README.md).
