# @chat9/widget-app — spike

**Status: PoC. Do not edit `src/ChatWidget.tsx`, `src/LinkSafetyModal.tsx`, `src/LoadingIndicator.tsx`, or `src/utils.ts` directly.**

Until PR 2 of the [widget extraction](https://app.clickup.com/t/86exevzyy) lands, the canonical source for these files is the dashboard:

| spike file | source of truth |
|---|---|
| `src/ChatWidget.tsx` | `frontend/components/ChatWidget.tsx` |
| `src/LinkSafetyModal.tsx` | `frontend/components/widget/LinkSafetyModal.tsx` |
| `src/LoadingIndicator.tsx` | `frontend/components/LoadingIndicator.tsx` |
| `src/utils.ts` (cn helper) | `frontend/components/ui/utils.ts` |

Bug fixes go to the dashboard copies; this spike will be re-synced or replaced during PR 2.

The only files that are spike-original and OK to edit:
- `src/main.tsx` — entry point. Currently mounts `ChatWidget` with hardcoded `botId="ch_POC"`. PR 2 will replace this with query-param / postMessage handshake parsing.
- `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.mjs`, `index.html`, `src/styles.css`, `package.json` — build config.

## Purpose

Validate that:
1. `react-markdown@10` + `remark-gfm` + `rehype-highlight` + `lucide-react` build cleanly under Preact via `preact/compat` aliasing.
2. The realistic gzipped bundle stays under the 150 kB fallback budget.

## Build / measure

```bash
pnpm --filter @chat9/widget-app build
```

Current measurement: **122.52 kB gzipped JS** (394.84 kB raw) + 4.09 kB gzip CSS. `highlight.js` (387 languages auto-detected) dominates — lazy-loading or a language subset is a tracked PR 2 optimization.
