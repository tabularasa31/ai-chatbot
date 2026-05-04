# @chat9/widget-app

Standalone build of the embeddable chat widget. Designed to run inside a cross-origin iframe (target: `widget.chat9.live`) loaded by `widget-loader`. Replaces the dashboard's `/widget` Next.js route once PR 2b cuts over.

## Status

Production-ready code path. After PR 2b lands, this is the **only** source of truth for the widget UI — `frontend/components/ChatWidget.tsx` and `frontend/app/widget/page.tsx` will be deleted.

Until PR 2b ships, the dashboard still serves its own copy at `/widget`. They are not auto-synced; the dashboard copy is now considered legacy.

## Runtime contract

The loader passes everything via URL query params on the iframe's `src`:

| Param | Required | Purpose |
|---|---|---|
| `botId` | yes | Public bot identifier (`ch_…`) |
| `apiBase` | yes | Origin of the dashboard API (e.g. `https://getchat9.live`). No trailing slash. All `/widget/*` and `/api/widget-*` calls are prefixed with this. |
| `parentOrigin` | recommended | Origin of the embedding page; required for postMessage identity handshake. Without it the widget falls back to anonymous mode. |
| `locale` | optional | BCP-47 locale for UI strings; defaults to `navigator.language`. |
| `siteUrl` | optional | Marketing-site URL for the "Powered by Chat9" footer link. Defaults to `https://getchat9.live`. |

If `botId` or `apiBase` are missing, the widget shows a "misconfigured" notice instead of mounting.

### postMessage identity handshake

```
widget-app  →  chat9:ready                        (sent on mount)
loader      →  chat9:identity { identityToken }   (identified mode)
            or chat9:no-identity                  (anonymous mode)
```

While waiting for the loader's response, the widget shows a "Loading…" frame. If the page is opened directly (no iframe parent) it resolves to anonymous immediately.

## Build / measure

```bash
pnpm --filter @chat9/widget-app build   # production build
pnpm --filter @chat9/widget-app dev     # local dev server (no backend wiring)
```

Current bundle (production, gzipped):

| Asset | Raw | Gzip |
|---|---|---|
| JS | ~395 kB | ~123 kB |
| CSS | ~15 kB | ~4 kB |

Most of the JS is `highlight.js` with all 387 languages bundled by auto-detect. Lazy-loading or a language subset is a tracked optimization.

## Source layout

| File | Purpose |
|---|---|
| `src/main.tsx` | Entry: parses query params, runs the postMessage handshake, mounts `ChatWidget`. |
| `src/ChatWidget.tsx` | The widget itself. Receives `apiBase` / `siteUrl` as props; no env-var reads. |
| `src/LinkSafetyModal.tsx`, `src/LoadingIndicator.tsx`, `src/utils.ts` | Supporting UI / helpers. |
| `src/styles.css` | Tailwind directives. |
| `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.mjs`, `index.html` | Build config. |

## Environment variables (dashboard side)

For widget-app to call the dashboard cross-origin, the dashboard's
`WIDGET_ALLOWED_ORIGINS` env var must include the widget-app origin
(comma-separated):

```
WIDGET_ALLOWED_ORIGINS=https://widget.chat9.live,http://localhost:5173
```

CORS is enforced in `frontend/middleware.ts`; identity is carried via Bearer token in the postMessage handshake, so cookies are never sent cross-origin and `Access-Control-Allow-Credentials` is never set.
