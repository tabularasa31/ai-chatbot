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

- `NEXT_PUBLIC_LANDING_DEMO_BOT_ID` — preferred public bot ID for the live landing-page demo chat

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
