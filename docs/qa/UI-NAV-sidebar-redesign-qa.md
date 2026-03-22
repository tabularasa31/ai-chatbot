# UI-NAV — Sidebar navigation redesign: QA checklist

**Branch:** `feat/sidebar-navigation-redesign`  
**PR scope:** Sidebar layout, Knowledge hub (`/knowledge`), Agents page (`/settings`), unified design system.

**Related docs:** [`IMPLEMENTED_FEATURES.md`](../IMPLEMENTED_FEATURES.md), [`PROGRESS.md`](../PROGRESS.md)

---

## Before testing

1. Run `npm run dev` from `frontend/` — app available at `http://localhost:3000`.
2. Make sure the backend is reachable (either Railway prod or local `uvicorn`).
3. Have a logged-in account ready. Two accounts useful for isolation checks (section G).

---

## A. Navbar

| # | Action | Expected |
|---|--------|----------|
| A1 | Open any app page (dashboard, logs, etc.) | Navbar is **fixed** at the top and does not scroll away |
| A2 | Inspect navbar content | Shows only: **Chat9** (link → `/dashboard`), user email, **Logout** button |
| A3 | No navigation links in navbar | Dashboard, Documents, Logs, etc. are **not** present in the navbar |
| A4 | Click **Logout** | Token cleared, redirect to `/login` |
| A5 | Email not verified → verification banner | Amber banner appears **below** the navbar, not hidden behind it |

---

## B. Sidebar — general

| # | Action | Expected |
|---|--------|----------|
| B1 | Open any app page | Fixed 200px left sidebar visible, does not scroll with content |
| B2 | Sidebar sections present | Main nav: Dashboard, Knowledge, Logs, Review, Escalations, Debug; **SETTINGS** label with: Agents, Response controls, Widget API; **Admin** (visible only for `is_admin` users) |
| B3 | Each item has a unique icon | No two items share the same SVG icon |
| B4 | Active item highlighted | Current route shows left violet bar + `bg-violet-50/08` background; other items dimmed |
| B5 | Navigate between pages via sidebar | Active state updates correctly on every page |
| B6 | Non-admin account | **Admin** entry is **not** shown in sidebar |
| B7 | Admin account | **Admin** entry is shown below the bottom divider |

---

## C. Layout & scroll

| # | Action | Expected |
|---|--------|----------|
| C1 | Open a long page (e.g., Logs with many sessions) | Scrolling content — navbar and sidebar stay fixed |
| C2 | Content area top padding | Content does not hide behind the fixed navbar (starts below 48px + 32px gap) |
| C3 | No white gap between navbar and sidebar | On scroll the sidebar top aligns exactly with the navbar bottom; no visible white stripe |
| C4 | Narrow viewport (< 900px) | Note any overflow; sidebar should not cover content completely — log findings |

---

## D. Knowledge page (`/knowledge`)

| # | Action | Expected |
|---|--------|----------|
| D1 | Click **Knowledge** in sidebar | Navigates to `/knowledge`, page title "Knowledge" with subtitle |
| D2 | Old URL `/documents` | Returns 404 or not found (route removed) |
| D3 | **External sources** cards visible | Four cards: GitHub, Confluence, Notion, URL Crawler — Confluence/Notion/URL Crawler are greyed out "Coming soon" and non-clickable |
| D4 | **Upload file** button | Opens native file picker; allowed types `.pdf .md .json .yaml .yml` |
| D5 | Upload a valid file | Row appears in table with type badge `file`, status `embedding…`, health `Pending` |
| D6 | After embedding completes | Status changes to `ready`, health shows Good/Fair/Needs attention dot |
| D7 | **Filter sources** input | Typing filters visible rows by filename; clearing restores full list |
| D8 | **Re-check** action on a ready file | Health status updates; button shows `…` while loading |
| D9 | **Delete** action | Confirmation dialog; on confirm row disappears |
| D10 | Delete during embedding | Delete button is disabled (opacity, no click) |
| D11 | Type badges | Files show blue `file` badge; future git rows would show green `git`; url rows show yellow `url` |
| D12 | Empty state | When no files: "No sources yet. Upload a file above." message in table |
| D13 | Filter with no matches | "No sources match your filter." message in table |

---

## E. Agents page (`/settings`)

| # | Action | Expected |
|---|--------|----------|
| E1 | Click **Agents** in sidebar | Navigates to `/settings`, page title "Agents" |
| E2 | OpenAI key **not configured** | Amber status banner "No API key — chat and embeddings are disabled" |
| E3 | OpenAI key **configured** | Green status banner "API key configured" |
| E4 | Input field placeholder | Shows `sk-...` |
| E5 | Type invalid key (not starting with `sk-`) and click **Save key** | Error message "OpenAI API key must start with 'sk-'" |
| E6 | Type valid key and click **Save key** | Green "Saved." banner appears briefly; status banner switches to green "API key configured" |
| E7 | Press Enter in input | Triggers save (same as clicking button) |
| E8 | Click **Update key** (when key already set) | Works the same as Save |
| E9 | Click **Remove key** | Key removed; status banner switches to amber |
| E10 | `/settings` is protected | Visiting without token → redirect to `/login` |

---

## F. Dashboard changes

| # | Action | Expected |
|---|--------|----------|
| F1 | Open `/dashboard` | Page shows: heading, API key card, (optional warning), Embed code card |
| F2 | OpenAI key **not set** | Amber banner: "OpenAI API key is not set — configure in Settings" with a link |
| F3 | Click link in banner | Navigates to `/settings` |
| F4 | OpenAI key **is set** | No amber banner on dashboard |
| F5 | **Quick links** section | Not present (removed) |
| F6 | OpenAI key form on dashboard | Not present (moved to `/settings`) |
| F7 | **Copy** button on API key | Copies key to clipboard; button text changes to "Copied!" for ~2s |
| F8 | **Copy embed code** button | Copies snippet; "Copied!" state |

---

## G. Design system consistency

Check each page below for uniform styling:

| Page | Card style | Primary button | Links | Inputs |
|------|-----------|---------------|-------|--------|
| Dashboard | `rounded-xl border border-slate-200` | `bg-violet-600` | — | — |
| Knowledge | same | `bg-violet-600` (Upload) | — | `border-slate-200 rounded-lg` |
| Agents | same | `bg-violet-600` | `text-violet-600` | `border-slate-200 rounded-lg` |
| Logs | same | `bg-violet-600` | `text-violet-600` | `border-slate-200 rounded-lg` |
| Review | same | `bg-violet-600` | `text-violet-600` | `border-slate-200 rounded-lg` |
| Escalations | same | `bg-violet-600` | `text-violet-600` | `border-slate-200 rounded-lg` |
| Debug | same | `bg-violet-600` | — | `border-slate-200 rounded-lg` |
| Response controls | same + `border-violet-400` active radio | `bg-violet-600` | — | — |
| Widget API | same | `bg-violet-600` | — | — |

For each page verify:
- [ ] No `shadow-md` on cards (replaced by border)
- [ ] No blue buttons (`bg-blue-600`)
- [ ] No black buttons (`bg-[#0A0A0F]`, `bg-slate-900`)
- [ ] Error banners have `border border-red-100 rounded-lg`
- [ ] Section headings (h2) are `text-base font-semibold text-slate-800`
- [ ] Page subtitles are `text-slate-500 text-sm`

---

## H. Navigation isolation / protected routes

| # | Action | Expected |
|---|--------|----------|
| H1 | Visit `/knowledge` without token | Redirect to `/login` |
| H2 | Visit `/settings` without token | Redirect to `/login` |
| H3 | Visit `/documents` (old URL) | Not found / 404 (no redirect defined) |
| H4 | Visit `/settings/disclosure` | Response controls page loads correctly |
| H5 | Visit `/settings/widget` | Widget API page loads correctly |

---

## I. Regression — existing features

| # | Area | Check |
|---|------|-------|
| I1 | Logs | Session list loads; selecting a session shows messages; thumbs feedback works; "Edit ideal answer" button saves |
| I2 | Review | Bad answers list loads; saving ideal answer works; "Show debug" expands retrieval info |
| I3 | Escalations | Ticket list loads; expand/collapse row; resolve with notes works |
| I4 | Debug | Question input → Run debug → answer + chunks table displayed |
| I5 | Response controls | Level loads; changing and saving works; "Saved." banner appears |
| I6 | Widget API | Status loads; "Generate signing secret" / "Rotate" works; one-time secret shown |
| I7 | Admin | Admin users see `/admin/metrics`; non-admin get redirected |
| I8 | Widget embed | Embed code on dashboard contains correct `clientId`; widget loads in iframe at `/widget` |

---

## J. Edge cases

| # | Scenario | Expected |
|---|----------|----------|
| J1 | Upload file > 50MB | Error message shown; no crash |
| J2 | Upload unsupported type (e.g., `.txt`) | File picker restricts or backend returns error |
| J3 | Knowledge page with no files and active filter | "No sources match your filter." (not broken layout) |
| J4 | Sidebar on a page not in nav (e.g., `/admin/metrics`) | Sidebar renders, no active item highlighted (or Admin highlighted) |
| J5 | Very long email in navbar | Truncates gracefully, does not break navbar layout |
| J6 | Browser back/forward navigation | Active sidebar state updates to match current URL |

---

*Add scenario notes or bugs found below this line.*
