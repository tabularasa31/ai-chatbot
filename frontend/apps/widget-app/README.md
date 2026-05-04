# @chat9/widget-app

Standalone build of the embeddable chat widget. Runs inside a cross-origin iframe at `https://widget.getchat9.live/v1/`, loaded by `@chat9/widget-loader` (which is itself co-deployed at `https://widget.getchat9.live/widget.js`).

## Status

Sole source of truth for the widget UI. The dashboard's old `/widget` Next.js route, `components/ChatWidget.tsx`, and `backend/static/embed.js` have been removed.

## Runtime contract

The loader passes everything via URL query params on the iframe's `src`:

| Param | Required | Purpose |
|---|---|---|
| `botId` | yes | Public bot identifier (`ch_…`) |
| `apiBase` | yes | Origin of the dashboard API (e.g. `https://getchat9.live`). No trailing slash. All `/widget/*` and `/api/widget-*` calls are prefixed with this. |
| `parentOrigin` | recommended | Origin of the embedding page; required for the postMessage identity handshake. Without it the widget falls back to anonymous mode. |
| `locale` | optional | BCP-47 locale for UI strings; defaults to `navigator.language`. |
| `siteUrl` | optional | Marketing-site URL for the "Powered by Chat9" footer link. Defaults to `https://getchat9.live`. |

If `botId` or `apiBase` are missing, the widget shows a "misconfigured" notice instead of mounting.

### postMessage identity handshake

```
widget-app  →  chat9:ready                        (sent on mount)
loader      →  chat9:identity { identityToken }   (identified mode)
            or chat9:no-identity                  (anonymous mode)
```

While waiting for the loader's response, the widget shows a "Loading…" frame. If the loader doesn't respond within `IDENTITY_HANDSHAKE_TIMEOUT_MS` (2500 ms by default; see `src/main.tsx`) it falls back to anonymous so the UI never deadlocks. If the page is opened directly (no iframe parent) it resolves to anonymous immediately.

## Build / measure

```bash
pnpm --filter @chat9/widget-app build   # builds widget-app + widget-loader + assembles dist/
pnpm --filter @chat9/widget-app dev     # local dev server (no backend wiring)
```

The production `build` script chains:

1. `vite build` for the widget UI → `dist/v1/index.html`, `dist/v1/assets/[hash].*`
2. `pnpm --filter @chat9/widget-loader build` for the loader IIFE → `apps/widget-loader/dist/widget.js`
3. `node scripts/copy-loader.mjs` → copies the loader into `dist/widget.js` so a single Vercel deploy serves both surfaces.

Current bundle (production, gzipped):

| Asset | Raw | Gzip | Budget |
|---|---|---|---|
| Widget JS (`/v1/assets/index-…js`) | ~388 kB | ~120 kB | 150 kB hard ceiling |
| Widget CSS | ~16 kB | ~4 kB | — |
| Loader (`/widget.js`) | ~6 kB | ~2.7 kB | 30 kB hard ceiling |

Most of the widget JS is `highlight.js` with all 387 languages bundled by auto-detect. Lazy-loading or a language subset is a tracked optimization.

## Source layout

| File | Purpose |
|---|---|
| `src/main.tsx` | Entry: parses query params, runs the postMessage handshake, mounts `ChatWidget`. |
| `src/ChatWidget.tsx` | The widget itself. Receives `apiBase` / `siteUrl` as props; no env-var reads. |
| `src/LinkSafetyModal.tsx`, `src/LoadingIndicator.tsx`, `src/utils.ts` | Supporting UI / helpers. |
| `src/styles.css` | Tailwind directives. |
| `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.mjs`, `index.html` | Build config. |
| `vercel.json` | Cache-control + CSP `frame-ancestors *` headers for `/v1/`, `/v1/assets/*`, `/widget.js`. |
| `scripts/copy-loader.mjs` | Post-build step that pulls the loader IIFE into the widget-app `dist/`. Asserts the 30 kB gzip ceiling. |
| `scripts/check-bundle-size.mjs` | Asserts the 150 kB widget-JS gzip ceiling. |

## Environment variables (dashboard side)

For widget-app to call the dashboard cross-origin, the dashboard's
`WIDGET_ALLOWED_ORIGINS` env var must include the widget-app origin
(comma-separated):

```
WIDGET_ALLOWED_ORIGINS=https://widget.getchat9.live,http://localhost:5173
```

CORS is enforced in `frontend/middleware.ts`; identity is carried via the postMessage handshake, so cookies are never sent cross-origin and `Access-Control-Allow-Credentials` is never set.
