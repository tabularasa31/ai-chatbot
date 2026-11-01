# DESIGN.md — Chat9 design system (v2.1, 2026-10-30)

Scope: marketing site + blog, auth pages, tenant dashboard, embeddable chat widget, client docs (fumadocs).
Every screen uses only the values below; no value is invented per screen.
Site, auth, dashboard and docs support two colour schemes (light, dark), chosen as in 4.1. The widget follows the tenant's `theme` embed option (`light` default, `dark`), never the visitor's system setting or the scheme cookie. The landing demo is the one place the site passes the scheme on: it starts the widget with `theme` set to the resolved site scheme and restarts it when the scheme changes. The widget still reads only its `theme` option. Tokens below are scheme-independent names; each has a light and a dark value.

Contrast figures were computed by hand with the WCAG 2.x relative-luminance formula. Re-verify with a tool when a token changes.

## 1. Typeface

- Sans: Geist Sans (variable, already in `frontend/app/fonts/GeistVF.woff`, self-hosted). Reason: neutral UI grotesque, readable at 12-14px, variable weights (one file), tabular figures, already shipped. Stack: `Geist, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`.
- Mono: Geist Mono (already in repo) for code, IDs, keys, embed snippets only. Two families total; nothing else.
- Widget: system font stack (no font download), decision 2026-10-16. Sans `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`; mono `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`. Chat content arrives in any language, which the system stack covers.
- Coverage measured with fontTools (2026-10-16) on `frontend/app/fonts`: `GeistVF.woff` has Cyrillic U+0410-044F 64/64, Latin Extended-A 117/127, Greek U+0391-03C9 only 4 of ~57 (effectively none); `GeistMonoVF.woff` has the same profile. Keep the bundled Geist files, no new dependency. Greek and any other uncovered script fall back to `system-ui` per character by design; this is accepted.
- `body` uses this stack; Arial and `font-['Inter']` are not used.

## 2. Type scale (6 sizes, 3 weights)

Weights: 400 regular (body), 500 medium (labels, buttons, nav, table headers), 600 semibold (headings only). No other weights, no all-caps. Letter-spacing exists only as heading tracking (below); body, UI and caption text keep the font's default.

| Token | Size / line height | Use |
|---|---|---|
| `text-caption` | 12 / 16 | meta, table headers, helper text, timestamps, badges |
| `text-ui` | 14 / 20 | default in dashboard and widget: controls, tables, nav, messages |
| `text-body` | 16 / 24 | default on marketing, blog, docs, auth; inputs use `text-body` everywhere except the dashboard (desktop only), which uses `text-ui` (14 / 20) |
| `text-h2` | 20 / 28, 600 | section heading, modal title |
| `text-h1` | 24 / 32, 600 | page title (dashboard, auth, docs page title) |
| `text-display` | 36 / 44, 600, from 768px (24/32 below) | marketing/blog page title only |

Heading map: display = marketing/blog H1; h1 = H1 everywhere else; h2 = H2; H3 and below = `text-body` 600. Headings of one level never differ between screens.
Tracking tightens as size grows: display -0.02em, h1 -0.015em, h2 -0.01em; H3 and below, body, ui and caption 0. The same values apply to docs prose headings.
Body line length 45-75 characters: prose containers on marketing and blog use `max-w-measure` (34rem, about 70-75 characters in Geist; `max-w-prose` at 65ch measured 88-96 characters because Geist is narrow).
Tables, amounts, counters, dates in columns: `tabular-nums`; numeric columns right-aligned, header aligned the same way.

## 3. Spacing, radius, elevation

Spacing (4px base): 4, 8, 12, 16, 24, 32, 48, 64 (Tailwind steps 1, 2, 3, 4, 6, 8, 12, 16). Marketing section gaps may also use 96 (`24`). Arbitrary values (`py-[7px]`, `mt-[13px]`) are not allowed.
Grouping: spacing and alignment first, 1px divider (`line`) second, container last.

Radius (override Tailwind `borderRadius`, semantic names so existing `rounded-*` meanings do not silently shift):

| Token | px | Use |
|---|---|---|
| `rounded-tag` | 4 | inline code, small tags |
| `rounded-control` | 8 | buttons, inputs, selects, nav rows |
| `rounded-surface` | 12 | cards, modals, drawer, banners, widget panel in inline mode |
| `rounded-bubble` | 16 | chat message bubbles, widget panel (bubble mode); the corner nearest the sender is `rounded-tag` 4 (bot and operator bottom-left, visitor bottom-right) |
| `rounded-full` | 9999 | avatars, count badges, icon buttons, widget composer pill, send, close and launcher |

Elevation: shadows only for things that float. Cards, sections, banners, auth card, header: no shadow.

| Token | Light | Dark | Use |
|---|---|---|---|
| `shadow-pop` | `0 4px 12px rgb(15 23 42 / 0.12)` | `0 4px 12px rgb(0 0 0 / 0.5)` | dropdown, popover, tooltip |
| `shadow-overlay` | `0 16px 40px rgb(15 23 42 / 0.16)` | `0 16px 40px rgb(0 0 0 / 0.5)` | modal, drawer |
| `scrim` | `#0A0A0F` at 60% | `#0A0A0F` at 60% | behind modal, drawer |

In dark, shadows are nearly invisible: floating layers also use `surface-alt` + 1px `line`.
Motion: state and colour changes take 120ms on a strong ease-out, `cubic-bezier(0.23, 1, 0.32, 1)`, which is also Tailwind's default transition (the built-in 150ms ease-in-out is replaced). Modal enter 200ms, same curve (opacity and scale 0.96 to 1, from the centre); drawer enter 200ms, `cubic-bezier(0.32, 0.72, 0, 1)` (slides from the right edge); the scrim fades with its panel. There is no exit motion: closing is instant; the two exceptions are Flash and the Copy button's "Copied", which fade out in 120ms. Buttons press in on `:active` with scale 0.97 (0.94 for 32px plates and round icon buttons, 0.985 for full-width buttons); nothing else scales: no hover scale, no scale on rows, cards, surfaces or the sidebar. Progress spinners (`Loader2`) are the only looping element and keep rotating under reduced motion, in the widget too; they carry state, not decoration. Skeletons are static (no pulse, no shimmer). Hover styles apply only on devices that can hover (Tailwind `hoverOnlyWhenSupported`); touch gets feedback on pointer-down through `:active`. Reduced motion: one global rule sets transitions to 0 and drops scale; enter animations become a 120ms opacity fade; spinners are unaffected. That rule is the single allowed `!important` in the main app and the widget, because utility classes outrank `*`. Widget buttons press in on `:active` (send, close and Copy code scale to 0.94, text buttons such as Try again and the action buttons to 0.97) over 120ms `cubic-bezier(0.23, 1, 0.32, 1)`; not when disabled or `aria-busy`, and not under reduced motion. The widget launcher presses to 0.94 over 120ms; the panel enters and exits on `cubic-bezier(0.32, 0.72, 0, 1)`, 200ms in and 120ms out. The widget loading indicator, and the send button's arrow-to-spinner swap, appear only after 200ms.

## 4. Colour roles

One accent (violet). Neutrals are Tailwind slate in light and the `nd` dark neutrals, lifted one step, in dark. Semantic colours only for their meaning. No gradients. Text colour is never expressed as opacity of another colour (`text-nd-text/50`); use the role.
Token names avoid `accent`, `primary`, `foreground`, `card` because the fumadocs preset already defines them (its `bg-accent` is a hover background and would turn solid violet).

### Light

| Role (Tailwind name) | Hex | Contrast |
|---|---|---|
| `canvas` page background | `#F8F9FA` | browser bar: the single `theme-color` meta is `#F8F9FA`, `#1A1A26` when dark (the dark `canvas`), set by the scheme boot script so the in-app choice wins over the OS; change all together |
| `surface` cards, inputs, modals, tables | `#FFFFFF` | - |
| `surface-alt` hover rows, table header, code, assistant bubble, skeleton | `#F1F5F9` | - |
| `line` dividers, card border (decorative, exempt) | `#E2E8F0` | - |
| `line-strong` control boundaries (input, select, checkbox) | `#7C8BA1` | 3.46 on surface, 3.29 on canvas, 3.16 on surface-alt |
| `line-button` secondary button outline | `#CBD5E1` | 1.48 surface, 1.41 canvas, 1.36 surface-alt (decorative: the button is identified by its 4.5:1+ label) |
| `fg` primary text | `#1E293B` | 14.62 surface, 13.88 canvas, 13.35 surface-alt |
| `fg-muted` secondary text | `#475569` | 7.58 surface, 7.19 canvas, 6.92 surface-alt |
| `fg-faint` placeholder, disabled, hints | `#586579` | 5.91 surface, 5.61 canvas, 5.40 surface-alt |
| `brand` accent: primary button fill, links, selected, focus | `#7C3AED` | 5.70 surface, 5.41 canvas, 5.20 surface-alt |
| `brand-hover` | `#6D28D9` | white on it 7.10 |
| `brand-active` | `#5B21B6` | white on it 8.99 |
| `brand-on` text on brand | `#FFFFFF` | 5.70 on brand |
| `brand-subtle` selected row/nav bg | `#EDE9FE` | brand text on it 4.80 |
| `focus` ring | `#7C3AED` (= brand) | 5.70 surface, 5.41 canvas (needs 3.0) |
| `success` / `success-subtle` | `#15803D` / `#F0FDF4` | 5.02 surface, 4.76 canvas, 4.58 surface-alt, 4.79 on subtle |
| `warning` / `warning-subtle` | `#A15C07` / `#FFFBEB` | 5.19 surface, 4.92 canvas, 4.73 surface-alt, 5.00 on subtle |
| `danger` / `danger-subtle` | `#B91C1C` / `#FEF2F2` | 6.47 surface, 6.14 canvas, 5.91 on subtle |
| `danger-hover` | `#991B1B` | white on it 8.31 |
| `danger-active` | `#7F1D1D` | white on it 10.02 |
| `danger-on` | `#FFFFFF` | 6.47 on danger |
| `info` / `info-subtle` | `#0369A1` / `#F0F9FF` | 5.93 surface, 5.63 canvas, 5.56 on subtle |

### Dark

| Role | Hex | Contrast |
|---|---|---|
| `canvas` | `#1A1A26` | - |
| `surface` | `#222233` | - |
| `surface-alt` | `#2B2B3F` | - |
| `line` (decorative, exempt) | `#3A3A50` | - |
| `line-strong` | `#7C7793` | 3.66 surface, 4.03 canvas, 3.23 surface-alt |
| `line-button` | `#4A4A63` | 1.82 surface, 2.01 canvas, 1.61 surface-alt |
| `fg` | `#FAF5FF` | 14.55 surface, 16.05 canvas, 12.88 surface-alt |
| `fg-muted` | `#A9A3BA` | 6.42 surface, 7.08 canvas, 5.68 surface-alt |
| `fg-faint` | `#9B95AD` | 5.43 surface, 5.98 canvas, 4.80 surface-alt |
| `brand` | `#A78BFA` | 5.74 surface, 6.33 canvas, 5.08 surface-alt |
| `brand-hover` | `#C4B5FD` | brand-on on it 10.70 |
| `brand-active` | `#8B5CF6` | brand-on on it 4.66 |
| `brand-on` | `#0A0A0F` | 7.26 on brand |
| `brand-subtle` | `#2D2552` | brand text on it 5.15 |
| `focus` | `#A78BFA` | 5.74 surface, 6.33 canvas |
| `success` / `success-subtle` | `#4ADE80` / `#1A3626` | 8.96 surface, 9.88 canvas, 7.53 on subtle |
| `warning` / `warning-subtle` | `#FBBF24` / `#3A2D16` | 9.35 surface, 10.31 canvas, 8.03 on subtle |
| `danger` / `danger-subtle` | `#F87171` / `#432227` | 5.65 surface, 6.23 canvas, 5.07 on subtle |
| `danger-hover` | `#FCA5A5` | danger-on on it 10.40 |
| `danger-active` | `#EF4444` | danger-on on it 5.25 |
| `danger-on` | `#0A0A0F` | 7.15 on danger |
| `info` / `info-subtle` | `#38BDF8` / `#183246` | 7.29 surface, 8.04 canvas, 6.19 on subtle |

In dark, the `*-subtle` fills sit above `surface` (lighter, not inset). `fg-faint` is not used on `brand-subtle` (4.87:1 there); use `fg-muted` (5.76:1).

Contrast changes made to the existing look:
- Accent: the product used magenta `#E879F9` (marketing, auth, sidebar) and violet-500/600 (dashboard, widget). Magenta on a light background is ~2.4:1 and fails. The single accent is violet: `#7C3AED` light, `#A78BFA` dark. The logo asset keeps its own `#E879F9`/`#38BDF8` (brand mark, not UI); see Decisions.
- Cyan (`#67E8F9`, `#5EC8FF`) link/tag colours on blog/docs are retired: links use `brand`; one accent only.
- `slate-400` text (labels, ~2.56 on white) and `gray-400` text in the widget (~2.5) fail 4.5 and are replaced by `fg-faint`.
- White text on `amber-600` buttons (~3.2) fails; warning is never a button fill.
- Light brand-subtle is `#EDE9FE` (was `#F5F3FF`, 1.10:1 against surface, invisible as a selection fill; now 1.19:1 with 4.80:1 brand text). Light warning is `#A15C07` (more amber, so Failed and Fair statuses no longer read as the same red-brown).
- Disabled controls: `surface-alt` fill + `fg-faint` text, no opacity trick. Contrast-exempt by WCAG, but still readable.

### Widget-only roles

`accent` and `accent-on` exist in the widget build only. `accent` is the tenant `color` (validated `#RRGGBB`, passed to the iframe as the `accent` URL param next to `theme` and `mode`), default `brand`; `accent-on` is white or `#000000`, whichever contrasts more with `accent`, compared directly (default `brand-on`; at least 4.58 for any colour, worst case at the crossover). They fill the send button, the visitor bubble and the launcher and nothing else: text, links, focus, status, and the bot and operator bubbles never take the tenant colour. Hover and active for a tenant colour are `brightness(.92)` and `brightness(.85)`; the default uses `brand-hover` and `brand-active`. Not guaranteed: accent fill against `surface` for an arbitrary tenant colour (the glyph on the fill stays readable).

### 4.1 Scheme selection (site, auth, dashboard, docs)

- Browser tab title is `<Page> — Chat9` on every route (site, auth, docs, blog, dashboard, 404). `<Page>` is the page h1 or, in the dashboard, the sidebar section name. The suffix lives only in the root layout `title.template`; pages give just `<Page>`. The landing is the exception: `Chat9 — AI support chatbot for your docs`.
- One shared setting with three values: System (default), Light, Dark. System follows `prefers-color-scheme`; if the browser gives no signal, light.
- Stored in one cookie `chat9-scheme` (`system|light|dark`; `Path=/`, 1 year, `SameSite=Lax`, readable by script because the scheme menu writes it). Marketing, auth, dashboard and docs read the same cookie, so the choice carries across them; site, auth, dashboard and docs are one Next.js app on one host, so a host-only cookie (no `Domain`) is enough.
- No flash: the server renders no scheme class, so pages stay statically cacheable. A tiny blocking script in the root layout's `<head>` reads the cookie, resolves System through `matchMedia`, and sets `class="dark"` on `<html>` before first paint; it re-resolves when the OS preference or the cookie changes (the scheme menu dispatches `chat9-scheme-change`). `<html>` carries `suppressHydrationWarning`. A surface that forces its own scheme (until it is migrated) renders `[data-scheme-forced]`; while that element is mounted the script does not touch `<html>`. A migrated surface calls `resolveScheme()` (`frontend/lib/scheme.ts`) when it mounts, so client-side navigation from a forcing surface restores the stored scheme. A surface that has migrated to tokens renders `data-scheme-surface`, which sets `color-scheme` to match the resolved scheme so native controls and scrollbars follow.
- Docs: opts into the scheme. `forcedTheme` and fumadocs' own toggle are gone (`RootProvider theme.enabled = false`, `disableThemeSwitch`); fumadocs reads the same `.dark` class, so there is no second storage key and no second control. The docs layout renders `SchemeSurface` (`data-scheme-surface` + `resolveScheme()` on mount).
- Scheme menu (`ui/theme-menu.tsx`, one component everywhere; `surface` picks `canvas` or `chrome` styling): a quiet icon trigger that opens a three-row menu. Trigger: no border and no fill at rest, 44x44 hit area with a 32px round visual and a 16px lucide glyph showing the chosen setting (`Monitor` System, `Sun` Light, `Moon` Dark); rest `fg-muted` (`chrome-fg-muted` on chrome), hover and menu open `fg` on a `surface-alt` (`chrome-hover`) fill; pressed: the plate takes scale 0.94 and the next fill (`line`; `chrome-press` on chrome); focus ring 2px around the visual, offset in the surface colour (`focus` / `chrome-accent`); accessible name `Colour scheme: <choice>`, `aria-haspopup="menu"`, `aria-expanded`, tooltip with the same text. Menu: `role="menu"`, 160px, `p-1`, `rounded-control`, `shadow-pop`, three 44px `role="menuitemradio"` rows (System, Light, Dark; 16px icon, `text-ui` 500 label, 16px `Check` on the current one), no arrow, no scrim, 4px from the trigger's visual edge, opens on the preferred side and flips when it has no room. Canvas: light `surface` + 1px `line`, dark `surface-alt` + 1px `line`; row hover `surface-alt` (dark: `canvas`); current row `brand`. Chrome (same in both schemes): `chrome` + 1px `chrome-line`, row hover `chrome-hover`, current row `chrome-accent`. Keys: click, Enter, Space or ArrowDown on the trigger opens it on the current row; ArrowUp/ArrowDown wrap, Home/End jump, Enter/Space/click applies at once (no save step), closes and returns focus to the trigger; Escape closes and returns focus; Tab closes without selecting; an outside press closes.
- Placement, one per surface: dashboard = top bar account area, between the e-mail and Log out, opens below; marketing + blog = footer, last item of the links row (after Contact support, same 24px rhythm, outside the `nav` landmark), no caption; auth = below the card, trigger glyph aligned to the card edge; docs = foot of the docs sidebar (inside the mobile drawer on narrow widths); all but the dashboard open upward. Not in any header except the dashboard top bar, not in the widget.

### 4.2 Chrome (dashboard sidebar and top bar, widget header)

Dark in both schemes, same values in both; the working area keeps its scheme. Only sidebar, top bar, the widget header and what sits on them (CountBadge, scheme menu, Log out, widget close button and attribution link) use these tokens, never content. Exception: in the widget header `chrome-line-strong` is allowed as text for the 12px "Powered by Chat9" line (4.62 on `chrome`, passes 4.5 with 0.12 to spare; hover `chrome-fg-muted` 8.12). Order in dark: chrome < canvas < surface < surface-alt.

| Token | Hex | Use |
|---|---|---|
| `chrome` | `#0A0A0F` | fill of sidebar and top bar; also the dark plate behind artwork drawn for dark backgrounds (blog covers) |
| `chrome-fg` | `#FAF5FF` | wordmark, hover text |
| `chrome-fg-muted` | `#A9A3BA` | inactive nav, e-mail, scheme trigger at rest |
| `chrome-line` | `#2E2E3E` | dividers, quiet badge fill (decorative, exempt) |
| `chrome-line-strong` | `#7C7793` | "Powered by Chat9" text in the widget header |
| `chrome-hover` | `#1E1E2E` | row and button hover |
| `chrome-press` | `#2A2A3D` | pressed fill for rows and plates (one step past `chrome-hover`) |
| `chrome-active` | `#2A2147` | selected row fill |
| `chrome-accent` | `#A78BFA` | selected text and icon, leading bar, focus ring, brand badge fill |
| `chrome-accent-fg` | `#0A0A0F` | text on `chrome-accent` |

Contrast (text >= 4.5, UI >= 3):
- `chrome-fg` on chrome 18.41, hover 15.28, active 13.91, `chrome-line` 12.41.
- `chrome-fg-muted` on chrome 8.12, hover 6.74.
- `chrome-fg` on chrome-press 13.07, `chrome-fg-muted` 5.76, `chrome-accent` 5.15.
- `chrome-accent` on chrome 7.26, hover 6.03, active 5.48; `chrome-accent-fg` on `chrome-accent` 7.26.
- `chrome-line-strong` on chrome 4.62 (widget attribution text only; lowest chrome text pair).
- Selection does not rely on the fill (active vs chrome 1.32): text and icon colour, the 2px `chrome-accent` leading bar (7.26) and `aria-current`. There is no `chrome-focus`: the light `focus` is 2.88 on `chrome-hover` and 2.62 on `chrome-active` (fails 3:1), so chrome uses `chrome-accent`.
- The logo mark keeps its original colours on chrome (`#E879F9` 8.03, `#38BDF8` 9.22); the wordmark is `chrome-fg`.

## 5. Tailwind mapping

Implementation shape: CSS variables (`--c-canvas` ... as space-separated RGB) on `:root` for light and on `.dark` for dark (the widget build emits both sets, dark under `html[data-theme="dark"]`, chosen by the `theme` URL param; the widget also reads the `accent` and `position` URL params, and uses `canvas` as the message-area fill in both themes; its dark surfaces are one step lighter than the dashboard's: surface `#222233`, surface-alt `#2B2B3F`, input fill `#1A1A26`, line `#3A3A50`, line-strong `#7C7793`, fg-faint `#9B95AD`, brand-subtle `#2D2552`, danger-subtle `#432227`, info-subtle `#183246`); `tailwind.config.ts` `darkMode: "class"`; colours declared as `rgb(var(--c-x) / <alpha-value>)` in the shared `tailwind-brand.ts` so dashboard and widget-app builds stay in sync. `tailwind.config.ts` and the widget config set `future.hoverOnlyWhenSupported`, so every `hover:` and `group-hover:` utility is wrapped in `@media (hover: hover) and (pointer: fine)`. The ten chrome keys are one scheme-independent table (`CHROME_COLORS`) emitted on `:root` by `chromeTokensPlugin`, which only the dashboard config registers; the widget build registers the plugin too, for the header and code blocks. The fumadocs preset already switches on the `.dark` class: feed its `light` and `dark` slots with the two scheme values (derive HSL from the hex, do not hand-maintain). The `nd-*` palette is not used.

Fumadocs variable -> token: `background`=canvas, `foreground`=fg, `muted`=surface-alt, `muted-foreground`=fg-muted, `card`/`popover`=surface, `border`=line, `primary`=brand, `primary-foreground`=brand-on, `secondary`=surface-alt, `accent`=surface-alt, `accent-foreground`=fg, `ring`=focus. `accent` is surface-alt, not brand-subtle: fumadocs uses it for hover and selected-result fills while the active sidebar row is `primary/10`, and with `brand-subtle` the two would look the same.

Names: `bg-canvas`, `bg-surface`, `bg-surface-alt`, `border-line`, `border-line-strong`, `text-fg`, `text-fg-muted`, `text-fg-faint`, `bg-brand hover:bg-brand-hover active:bg-brand-active text-brand-on`, `bg-brand-subtle`, `ring-focus`, `text-success bg-success-subtle` (same for warning, danger, info), `bg-danger text-danger-on hover:bg-danger-hover active:bg-danger-active`, `border-line-button`, `active:bg-line`, `scale-press|press-sm|press-wide`, `bg-scrim`; chrome: `bg-chrome border-chrome-line text-chrome-fg text-chrome-fg-muted hover:bg-chrome-hover bg-chrome-active text-chrome-accent bg-chrome-accent text-chrome-accent-fg bg-chrome-press text-chrome-line-strong ring-chrome-accent ring-offset-chrome shadow-chrome-selected` (2px inset leading bar). Sizes: `text-caption|ui|body|h2|h1|display`. Also `shadow-pop`, `shadow-overlay`, `rounded-tag|control|surface|bubble`. Control height `h-11` (44px).

Data rules (`app/globals.css` utilities, `lib/format.ts`): `break-anywhere` is `overflow-wrap: anywhere` and replaces `break-all` / `break-words` on every piece of user data (e-mails, URLs, names, error text); unlike `break-words` it also lowers the min-content width, so flex/grid parents still need `min-w-0` and trailing actions `shrink-0`. Dates, counts and ticket numbers are `whitespace-nowrap tabular-nums` and never truncated; a table column that holds a date is sized to the date and the text column yields. Counted nouns go through `pluralize(count, one, other)` (`Intl.PluralRules("en")`, noun by the total, verb by the number it describes: "1 of 1 conversation was escalated.", "0 of 5 conversations were escalated."); no hard-coded `s`. A provider error (failed URL source) is never shown raw in a row: the row says "Couldn't read this page" in `danger` with a Ghost "Show details" / "Hide details" (`aria-expanded`), and the full text opens instantly in a `surface-alt` block with `break-anywhere`. Money uses `Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })`, counts `toLocaleString()`.

Old value -> token:

| Old | New |
|---|---|
| `#F8F9FA` (app layout) | `canvas` |
| `bg-white` (cards, widget body/input area) | `surface` |
| `bg-slate-50`, `bg-slate-100`, `hover:bg-slate-200` btn, `bg-gray-100` assistant bubble, `bg-gray-50` widget input | `surface-alt` |
| `border-slate-200`, `border-gray-200`, `border-slate-300` (dashed empty state) | `line` (empty state loses the dashed box) |
| `text-slate-800`, `slate-900`, `gray-800`, `gray-900`, `#0F172A` | `fg` |
| `text-slate-600/700`, `text-slate-500`, `gray-500` | `fg-muted` |
| `text-slate-400`, `gray-400`, `#64748B`, `#A8A3B8` | `fg-faint` (not for body text) |
| `bg-violet-600`, `violet-500` (btn, send), `bg-slate-900 text-white` (primary buttons in widget-settings), `#a855f7` (DashboardWidgetLoader) | `brand` (+ hover/active) |
| `hover:bg-violet-700/600` | `brand-hover` |
| `violet-50`/`violet-200` operator bubble, `#f3e8ff` and `blue-600` visitor bubble, `nd-accent/[0.08]`, `/20` badge bg | `brand-subtle` |
| `nd-accent #E879F9`, `nd-accent-hover #f099fb`, `#9D8FCF` | `brand`, `brand-hover` |
| `from-nd-accent to-violet-500` avatar gradient | solid `brand` |
| `nd-base #0A0A0F`, `nd-base-alt #12121A`, `nd-surface #1E1E2E`, `nd-border #2E2E3E`, `border-white/[0.07]` | `chrome` (dashboard sidebar and top bar) or `canvas`, `surface`, `surface-alt`, `line`, `line` (dark values, lifted one step) |
| `surface`/`line`/`fg*`/`brand-subtle` classes inside Sidebar, Navbar, CountBadge | `chrome*` (4.2) |
| `nd-text #FAF5FF`, `nd-text/80`, `/60`, `/50`, `/40`, `/20` | `fg`, `fg-muted`, `fg-muted`, `fg-muted`, `fg-faint` (placeholder), none (remove) |
| `nd-success/danger/info`, `bg-red-50 border-red-200 text-red-800`, `#FFF1F2/#FECACA/#991B1B` | `success`/`danger`/`info` with `-subtle` bg |
| `bg-amber-600` (WidgetIdentityKeyCard) | button by meaning: secondary or destructive, never warning-filled |
| cyan `accent` in fumadocs preset, `#5EC8FF`, `#67E8F9`, `#38BDF8` link/tag | `brand` |
| `text-[10px]`, `[11px]`, `[13px]`, `text-xs uppercase tracking-*` (Sidebar "Configure", StatCard label, widget operator label) | `text-caption`, sentence case, `fg-muted`, weight 500 |
| `text-2xl font-semibold` page titles, `text-base font-semibold` section titles | `text-h1`, `text-h2` |
| `rounded-md` 6 / `rounded-lg` 8 | `rounded-control` |
| `rounded-xl` 12, `rounded-[28px]` | `rounded-surface` |
| `rounded-2xl` bubbles | `rounded-bubble` |
| `shadow-sm`, `shadow-md` on cards and auth card, `shadow-[0_24px_80px_...]` | none (floating layers: `shadow-overlay`) |
| `hover:scale-*`, `transition-all`, `animate-pulse` | colour transition only (press scale on buttons only; spinners are the one loop) |

## 6. Components (only those in the product today)

General: every interactive element has hover, active, focus-visible, disabled; hit area at least 44x44 (`h-11`; icon buttons `h-11 w-11`, glyph 20px). Inline text links inside prose are exempt. No action is available only on hover; row actions are always visible. One primary action per screen (or per modal/form section).

Focus: `focus-visible` only, 2px `focus` ring with 2px offset in the underlying surface colour; never `outline-none` without this ring. On chrome (`offset="chrome"`, `FOCUS_RING.chrome`): `chrome-accent` ring, offset `chrome`. Hover: one colour step (never scale/translate). Active: one step past hover (primary `brand-active`, destructive `danger-active`, secondary and ghost fill `line`, chrome `chrome-press`), plus the button press scale of section 3 on Buttons only. Disabled and busy buttons show no hover, no press fill and no scale. Disabled: see 4. Widget composer exception: the input's 1px border becomes a 2px `focus` border (inset shadow, no layout shift), no outer ring, no offset ring; other inputs keep the ring.

Buttons (`ui/button.tsx`: `Button` for `<button>`, `buttonClasses` for links that look like buttons; one definition of height 44, radius, focus ring and the variants below, so no component copies the class strings; `offset` names the surface the ring offset matches). 14/20, 500, `px-4`, `rounded-control`, label is a verb describing the result: "Save widget settings", not "Submit":
- Primary: `brand` fill, `brand-on` text. Link CTAs (`<a>`) look identical.
- Secondary: `surface` fill, 1px `line-button` (lighter than input boundaries on purpose: the label identifies the button), `fg` text; hover `surface-alt`; active `line` fill with a `line-strong` border. No icon-only Secondary (use Ghost). Cancel, Copy, Back.
- Destructive: `danger` fill, `danger-on` text. Only in the confirm step of a destructive flow (`ConfirmDialog`), with the consequence stated next to it. Never the default focus in a modal. Hover `danger-hover`, active `danger-active`.
- Ghost: no fill, `fg-muted`, hover `surface-alt`, active `line` fill with `fg` text. On chrome (Log out, built like the scheme menu trigger beside it): a 44px hit area around a 32px `rounded-control` plate (`px-3`, `CHROME_PLATE`) that carries `chrome-fg-muted`, the `chrome-hover` fill with `chrome-fg` text on hover, the `chrome-press` fill on press, and the focus ring, so the ring stays inside the 48px bar. Icon-only (`size="icon"` = 44x44) for glyph actions such as close; with a text label (`size="default"`) for quiet row actions such as Edit, Reject, Dismiss, Discard draft. A Ghost label that starts a row under a text column takes `bleed="start"` (`-ml-4`) so the label aligns with the text edge above it; the hit area stays 44px and the hover plate extends 16px into the gutter, so the container must not clip it. Reason: a row with several text actions cannot make all of them Secondary without the row competing with the page's primary action.
- Disabled: `surface-alt` fill and `fg-faint` text; Ghost keeps no fill and its label or glyph drops to `line-strong` (3.46 light, 3.66 dark), visibly fainter than the enabled `fg-muted` (chrome Ghost: `chrome-line-strong`), `cursor-not-allowed`. Widget send follows this rule when the input is empty (grey, `disabled`) and takes the `accent` fill once there is text; while a request is in flight it keeps the `accent` fill with a spinner (`aria-busy`, clicks ignored).
- Loading (`busy` prop): a 16px spinner (lucide `Loader2`, only rotating element allowed) overlays the button centre while the label is hidden (`opacity-0`, still in the accessible name), so width never changes; `aria-busy`, clicks ignored, focus kept. The label never changes text while busy; progress such as "Uploading 2 of 5" goes in a separate `role="status"` line next to the button.
- Order in a row: secondary left, primary right; on the auth card the primary is full-width.

Text input and select: 44px high, `px-3`, `surface` fill, 1px `line-strong`, `rounded-control`, `fg` text, `fg-faint` placeholder. Label above (14/20, 500, `fg`), never placeholder-as-label. Inputs use `text-body` (16/24) everywhere except the dashboard (desktop only), which uses `text-ui` (14/20); the widget composer uses `text-ui` on fine pointers and `text-body` (at least 16px) on any `(pointer: coarse)` device, whatever the width or mode (prevents iOS zoom). Select is the native `<select>` with a 16px lucide chevron. Textarea same, `resize-none` (no native grip; it scrolls inside, the inbox composer starts at 2 rows and grows with its text up to `max-h-44`, then scrolls); the one exception is the Answer drawer's Markdown answer, which sets `resizable` (vertical only) because it holds long authored text. `mono` sets Geist Mono for Markdown or code content. Error: border `danger`, message below in `danger` 14/20 linked by `aria-describedby`; the user's input is kept. Helper text (12/16 `fg-muted`) only for a format, limit or non-obvious rule. Input takes a `ref` (the form reads `validity` from it instead of querying the DOM).

Checkbox and RadioGroup (`ui/choice.tsx`, new; Settings uses them for "Use only my own prompt", Link safety and Response detail): 20px native control with `accent-brand`, inside a full-width label row of at least 44px (`rounded-control`, hover `surface-alt`); label `text-ui` 500 with optional `text-caption fg-muted` helper or description under it, so the whole row is the target. RadioGroup is `role="radiogroup"` with an `aria-label`; the selected row is `brand-subtle`. Focus: `focus-visible` ring on the row (`has-[:focus-visible]`). Rows are not bordered cards. Reason: the rule above had no component, and three Settings screens needed one.

Card: only for a standalone, actionable object (a source, a document, an inbox conversation): `surface` + 1px `line`, `rounded-surface`, `p-4`, no shadow. Never inside another card. Page sections (settings, forms) are not cards: heading, spacing, `line` divider. The auth form container is the one allowed container. StatCard-style metric tiles exist today; use only where a number is the object the user acts on, otherwise a plain labelled value.

Count badge (`ui/count-badge.tsx`, new): number pill in a nav row. `rounded-full`, `h-5 min-w-5 px-1.5`, `text-caption` 500, `tabular-nums`; above 99 shows `99+`; renders nothing at 0 (also while loading or on error); a visually hidden suffix carries the meaning ("3 need attention"). Sits on chrome: tone `brand` (`chrome-accent` fill, `chrome-accent-fg` text) only for what needs a person; tone `quiet` (`chrome-line` fill, `chrome-fg` text) for the rest. Reason: the sidebar shows it twice and the badge rule (one count, one definition) must live in one place.

Modal and drawer: `surface`, `rounded-surface`, `shadow-overlay`, `scrim`. Modal `max-w-md`, `p-6`, title `text-h2`, only for a decision or a consequence that must be confirmed (link-safety confirm, destructive confirm). Drawer (document preview) from the right, `max-w-xl`, full height, same chrome. Both: focus moves in, is trapped, returns to the trigger on close; Escape and a visible close control (lucide `X`, 44px) close; the scrim click closes unless there is unsaved input. Buttons bottom-right per button order. Enter motion: section 3 (CSS keyframes on the native dialog and its `::backdrop`; under reduced motion the panel fades 120ms with no scale or slide). Closing is instant. A press that starts on a button or field inside and is released over the scrim does not close it: only a press that also started on the scrim does.

Table: header `text-caption` 500 `fg-muted`, sentence case, 1px `line` below; rows separated by 1px `line`, no zebra, no per-cell borders; hover `surface-alt`; row height 44 when the row has controls, 40 otherwise; cell padding `px-4`. Numbers and dates `tabular-nums`, right-aligned with their header. Long text wraps in the first column; IDs/keys in Geist Mono and truncated with the full value in a tooltip. Empty, loading, error render inside the table region (below).

Banners and notices, by urgency (only the highest tier gets a prominent treatment):
1. Page banner (top of `<main>`): only for a condition that blocks the product and that the tenant can fix (e.g. their own key invalid). `danger-subtle` or `warning-subtle` bg, 16px lucide icon in the semantic colour, `fg` text (14/20), one action link/button, `role="alert"`, 1px semantic border. At most one visible; highest severity wins. No banners for our own infrastructure degradation (infrastructure monitoring stays internal).
2. Inline notice (in a form/section): `*-subtle` bg, icon, `fg` text, no border, `role="status"`. Info and success use this tier.
3. Confirmation of a completed action ("Saved"): plain `success` text with a check icon next to the button, `aria-live="polite"`, disappears after 4s. No toast component exists; none is added.
Field errors: see input.

Page banner (`ui/page-banner.tsx`, new, section 5 tiers 1 and 2): one row at the top of `<main>`; `danger-subtle` or `warning-subtle` fill, 1px semantic border, 16px `TriangleAlert`, bold lead sentence plus one sentence in `fg` 14/20, optional Secondary action (44px; `ExternalLink` icon and `rel="noopener noreferrer"` when it leaves the app). `role="alert"` for danger, `role="status"` for warning. Reason: replaces three hand-built banners (OpenAI alert, e-mail strip above the navbar, identity banner) so urgency is defined once; blocking conditions (key invalid, quota exhausted, key not set) get the prominent treatment, the e-mail warning is quieter and shows only when nothing blocks. Roles without the fix (operators) see the same text without the action.

Inline notice (`ui/inline-notice.tsx`, new, section 5 tier 3 and confirmations): `info-subtle` fill, no border, 16px `Info`, `fg` text with text links, `role="status"`, optional 44px `X` dismiss (`aria-label` "Dismiss"). Reason: announcements and confirmations must never use alert styling; this is the single quiet notice. Dismissal of the widget identity announcement is remembered in the browser's `localStorage` (`chat9:widget-identity-banner-dismissed`), no expiry.

Inline notice tones: `tone="info"` (default, `info-subtle`, `role="status"`), `"warning"` (`warning-subtle`, `role="status"`) and `"danger"` (`danger-subtle`, `role="alert"`), same layout and no border. An optional `action` slot holds one 44px control (Undo, Reload) before the dismiss button. Reason: a failed action needs the tier-2 notice without banner weight, and some notices carry one remedy.

Inline notice `singleLine`: the text is one `truncate` line (full text in `title`), icon centred, 10px vertical padding, 40px tall. For a notice that must stay visible without eating the space of the content under it (the Inbox "Answered by email" line); its text is a plain string, put the part least needed last so end-truncation cuts that.

Confirm dialog (`ui/confirm-dialog.tsx`, new): the Modal rule above, built on native `<dialog>` (focus trap, Escape, focus return come from the platform). Title states the object ("Delete \"guide.pdf\"?"), one body sentence states the consequence, Cancel (Secondary, left, initial focus) and the confirm button (Destructive or Primary, right) named for the result. While the request runs the confirm button is busy, Cancel and Escape are disabled and the dialog stays open; a failure shows a danger `InlineNotice` inside it. The body is a `div`, so it can hold a list. Optional `confirmText` adds a labelled `Input` `Type "{text}" to confirm` and keeps the confirm button disabled until the typed value, ignoring surrounding spaces, matches exactly (case-sensitive) (Delete tenant only). Reason: native `confirm()` cannot state a consequence, be styled or show a failure.

Drawer (`ui/drawer.tsx`, new): the Drawer rule above as a component: native `<dialog>` aligned right, header with wrapping title, optional meta and actions, and a 44px close button; body is the caller's. Optional unsaved-changes guard: `canClose={false}` makes Escape and the scrim click keep the drawer open and call `onCloseBlocked` (the caller opens a `ConfirmDialog`); the close button always calls `onClose`, so the caller routes it through the same guard. Reason: the document preview and the Unanswered answer drawer share chrome, focus handling and scrim, and it removes the blur and heavy shadow of the old drawer.

Fields (`ui/field.tsx`, new): `Input`, `Select`, `Textarea` implementing the Text input rule: visible label above (`hideLabel` makes it screen-reader-only for toolbar filters), optional helper, optional error (border `danger`, message linked with `aria-describedby`, `aria-invalid`). Reason: many fields on Knowledge; one definition keeps height, border and focus ring identical.

Skeleton (`ui/skeleton.tsx`, new): `Skeleton` is a `surface-alt` block with no shimmer and no pulse; `SkeletonRegion` wraps a set of them, renders nothing for 200ms, then reveals them with `role="status"` and `aria-busy`. `RowListSkeleton` is the Knowledge Unanswered recipe: 5 rows in `divide-y line`, each with a name bar (`h-6`, widths cycling `w-2/5`, `w-1/2`, `w-3/5`) over a `h-4 w-24` caption bar in `py-4` rows (about 76px, as a real Unanswered row), and on the right a `h-5 w-20` status bar and two `h-8 w-8` action blocks. `DocumentRowsSkeleton` is the Documents recipe: 5 real `<tr>` in the table's `tbody` (revealed after the same 200ms; the table carries `aria-busy`), built cell for cell as a file row: Source cell `px-4 py-3` with an `h-5` name bar over an `h-4 w-12` caption bar, `h-5 w-20` in Status, `h-5 w-24` right-aligned in Updated, two `h-8 w-8` blocks in the actions cell; a single-line row is 62px, as a real one. `FaqListSkeleton` is the FAQ recipe: rows of `py-4` with stacked bars `h-6` (question), an `h-5` answer line (`w-4/5`), an `h-4` one (`w-3/5`) and an `h-4` meta bar (`space-y-1`), then an `mt-2 h-11` action row with a `w-12` block at the start and a `w-20` block at the end (about 173px, as a real two-line entry); the Unanswered and FAQ skeletons carry the same `border-y line` as their real lists. Heights of the Documents and FAQ rows were measured in a browser; the others are estimated from the real rows' classes. `/dashboard` keeps the real page frame while it loads: a `max-w-2xl space-y-8` column (title bar inside the same `SkeletonRegion`, so nothing shows before the 200ms delay) with an `h-8 w-48` title bar, then `space-y-3` blocks at the heights of the real elements (section heading `h-7`, Bot ID code and Copy button on one line). Reason: the Loading rule needs the delay in one place; replaces `PageLoader` and "Loading..." lines on these pages.

Flash (`ui/flash.tsx`): `useFlash()` returns the current message and a setter that clears it after 4s; `FlashMessage` renders it as `success` text with a 16px `Check`, fading in over 120ms and out over the last 120ms of the 4s (opacity only, kept under reduced motion; the line keeps its height; a new flash restarts the fade-out at full opacity). The `aria-live="polite"` region stays mounted so the text is announced when it appears. Reason: the completed-action tier (section 6, Banners and notices, tier 3) is used on Knowledge and its drafts, and the 4s timer and live region must exist once.

Load error (`ui/load-error.tsx`): `danger-subtle` fill, no border, `TriangleAlert`, `fg` message, Secondary "Try again" (44px, spinner before the label while retrying), `role="alert"`; for a region whose data failed to load. Without an `onRetry` callback the button is omitted (form-level errors on the auth pages, where retrying means submitting again).

Auth field (`components/auth/AuthField.tsx`, new): label above, 44px input as in the text input rule, helper (`text-caption` `fg-muted`) that an error (`text-ui` `danger`, `danger` border) replaces. Wires `htmlFor`, `aria-invalid` and `aria-describedby` (error id, else helper id). Reason: the same wiring repeats in nine inputs on five auth pages and its absence caused the missing aria and the 1.23:1 field border; the dashboard has no field component yet and may adopt it.

SectionTabs (`components/ui/section-tabs.tsx`): the one tab strip, replacing the Knowledge pill group so Knowledge and Settings look and behave alike. Row of 44px tabs, `gap-1`, `text-ui` 500, `rounded-control`; selected `brand-subtle` fill + `brand` text, others `fg-muted` with `surface-alt` hover, `focus` ring. No bordered outer box and no solid `brand` fill, so a selected tab never competes with the page's primary button. Two modes: links (`href` per tab, `<nav aria-label>`, `aria-current="page"`) when each tab is a URL; buttons (`onChange`, `role="tablist"`, `aria-selected`, roving tabindex, Left/Right/Home/End) when tabs switch content in place. No icons on tabs. A tab may carry an optional `suffix` node (the Inbox "Needs attention" tab shows the same count badge as the sidebar).

Markdown message (`ui/markdown-message.tsx`, new): renders assistant and operator messages in the dashboard with the widget's markdown subset (`react-markdown` + `remark-gfm`: paragraphs, bold/italic/strike, lists, links, inline code, fenced code, GFM tables; images and raw HTML are not rendered). Block gap 8px; links `brand` underlined, open in a new tab with `rel="noopener noreferrer nofollow"` and no link-safety confirm; inline code and code blocks use a `surface` fill with 1px `line` (`rounded-tag` / `rounded-control`, `p-3`, horizontal scroll), no syntax highlighting; tables `text-caption` with `line` rules inside an `overflow-x-auto` wrapper. Reason: the widget's renderer lives in the widget build with light-only colours and a dark `slate-900` code block, which breaks in the dark scheme; the dashboard needs one token-styled renderer. Visitor messages stay plain text.

SettingsShell (`app/(app)/settings/_components/SettingsShell.tsx`): H1 "Settings" (`text-h1`), 16px below it the SectionTabs strip (Bot | Widget | Team | Seats), 24px below that the tab content, in a left-aligned `max-w-2xl` column. Needed because the four tabs live in two route trees (`/settings/**`, `/widget-settings`) that a single layout cannot cover, and so the strip and H1 stay on screen while a tab loads or fails. It also holds the owner gate: a non-owner sees the H1, "Only the tenant owner can open Settings." and a "Back to Inbox" link, with no tabs and without mounting the tab content (no owner-only requests fire). Tab content starts at `text-h2`; a tab name is never repeated as an H1.

Menu (`ui/theme-menu.tsx` is the first; new, no menu or popover existed): a small `role="menu"` list opened from an icon or text trigger, for choosing one of a few settings, not for navigation. 160px wide, `p-1`, `rounded-control`, `shadow-pop`, 44px rows with `rounded-tag`; arrows wrap, Home/End jump, Enter/Space select, Escape closes and returns focus to the trigger, Tab closes, an outside press closes; placed 4px from the trigger and flipped when there is no room. Quiet icon trigger: 44x44 hit area, 32px round visual, 16px glyph (same pattern as the widget close button in "Widget v2 exceptions"). Reason: the segmented switch with its border and caption was the loudest control on every page for a setting made once; a menu keeps it findable and quiet.

Marketing shell (`components/marketing/`, new): `MarketingShell` wraps every site and blog page (opts into the scheme, skip link, header, `<main id="content">`, footer); `SiteHeader` is static, 64px, text links Docs / Blog / Sign in (Dashboard when signed in) with a 44px menu button below 768 (`aria-expanded`, `aria-controls`, Escape closes and returns focus); `SiteFooter` carries the links and the one `ThemeMenu` of the site. Reason: the old Navigation was sticky with blur, had 28px targets and no skip link; nothing shared fit.

DefinitionRows: `<ol>`/`<ul>` of rows, title (`text-body` 600) in columns 1-4 and text in 5-12 from 768, stacked below, `divide-y line`, `py-6`. Used for setup steps and conditions on the landing page. Reason: replaces the identical-card feature grid with rows whose content differs.

CodeSnippet: `surface-alt` block, Geist Mono `text-ui`, horizontal scroll inside itself (focusable, `aria-label`), secondary Copy button via `ui/copy-button` (its label cell is as wide as the wider of the idle label and "Copied", so the button does not change width when it flips; "Copied" lasts 4s and fades out over the last 120ms like Flash, opacity only, kept under reduced motion; a second click restarts it at full opacity). Reason: `CodeBlockWithCopy` is dashboard-only (primary button, no scroll focus).

DemoSlot: bordered `surface` box (`h-demo` = 480 below 640, 560 from 640) holding the inline widget; skeleton after 200ms, error with Reload page after a failed or 10s-late loader; not rendered when no bot id is configured. Reason: the old block swallowed loader errors and showed an environment variable name to visitors.

BlogPostRow / BlogPostList: one row per post, meta line, title as the only link (stretched `::after`), excerpt, `divide-y line`, hover `surface-alt`. Tags are not shown. Reason: replaces the three-column card grid with a card, tags and a duplicate "Read article" link per post.

States for any data region (list, table, document list, inbox, analytics):
- Loading: nothing for the first 200ms, then skeleton blocks of the final layout in `surface-alt`, no shimmer; `aria-busy`. A whole-page spinner is not used; inline `Loader2` only for buttons and refetch.
- Empty: left-aligned statement of what is missing in plain words (`text-ui`, `fg-muted`) plus the one action that fixes it, if it exists. No illustration, no dashed box. Centering only when the empty region is the whole page body.
- Error: in place of the data, say what failed and what the user can do, plus a secondary "Try again" button; keep already-loaded data visible when a refetch fails.
- Success: the result itself plus the confirmation text above; no celebratory states.

## 7. Icons

lucide-react only (already used in dashboard and widget). Sizes 16 (inline with 14px text, nav, banners; widget send `ArrowUp` and header close) and 20 (icon-only buttons); stroke 2 (library default); colour inherits text colour. Allowed for: sidebar navigation, standard actions (copy, close, send, delete, external link, retry, chevron), banner/status severity. Not next to every heading, field or list item. Icon-only buttons need `aria-label` and a tooltip (existing `ui/tooltip`). Quiet icon-only triggers (the scheme menu) may use a 16px glyph in a 32px visual when the 44px hit area is kept. No emoji as icon or decoration, no avatar-with-gradient decoration. The 15px hand-drawn SVGs in the sidebar and the inline external-link SVG in the widget are replaced by lucide equivalents.

## 8. Surfaces

| Surface | Density | Text | Content width | Notes |
|---|---|---|---|---|
| Dashboard | compact | `text-ui` 14/20, titles `text-h1` | content `max-w-6xl` (1152), forms/settings column `max-w-2xl` (672) | navbar 48px, sidebar 208px, section gap 32, in-section gap 16; desktop only (from 1024px), narrower widths not designed yet. Sidebar and top bar are dark chrome in both schemes (4.2); menu: section 9 |
| Auth | comfortable | `text-body` | card `max-w-md` (448) | left-aligned form; centering only for short success/confirmation screens |
| Marketing + blog | airy | `text-body`, `text-display` | container `max-w-6xl`, prose `max-w-measure` | layout, sections and copy of the landing are decided by the later redesign under the CoC; this system supplies tokens only. No hero/feature-grid defaults |
| Docs | reading | `text-body` | fumadocs layout, p/li/dd/blockquote/headings capped at the 34rem measure on every page (OpenAPI pages included) | fumadocs preset fed from this file; prose rules come from `docsTypography` in `tailwind-brand.ts`; `docs.css` is geometry only (no colours, no `!important`): sidebar rows, TOC links, search trigger and dialog rows 44px; no dark chrome; code blocks use `github-light-high-contrast` / `github-dark-high-contrast` (`lib/code-themes.ts`) so every syntax colour is at least 4.5:1; API method and Deprecated badges are neutral (`surface-alt` + `fg`, mono 12px, `rounded-tag`), Required is `danger` on `danger-subtle`; generated OpenAPI text (e.g. caps "PUBLIC") is content and left as is |
| Widget | compact | `text-ui`, input 14/20 (16/24 on any coarse pointer) | fills the iframe; 380x600 default, resizable 280x360 to 700x820, full screen when a portrait viewport is 479px wide or less, or a landscape coarse-pointer viewport is 479px tall or less | both themes (tenant `theme` option, default light, `html[data-theme]` set from the URL before first paint); header always `chrome` (both themes, 4.2 tokens), 48px in bubble and inline mode; in phone full screen min 48px, the title clamped to 2 lines (1 in landscape), 52px with a one-line title and 72px with two; no divider in light, and dark adds a 1px `chrome-line` bottom border: title "Assistant" (`<h1>`, 16/20, 600) with "Powered by Chat9" as a 12px second line that links to the site, and a 32px close button with a 44px hit area; while the resize grip shows, the header leaves 32px clear on the grip side (not rendered in inline or standalone mode); no footer; message area `canvas`; composer `surface`, no top line, 40px pill (`rounded-full`, 1px `line` border, 2px `focus` border when focused) and a 36px send with a 44px hit area (`ArrowUp`, `accent` fill with text, grey with an empty input, spinner while busy); bot bubble `surface` + 1px `line`, visitor bubble `accent`, operator `surface-alt` + 1px `line` with an "Operator" caption (the hairline is a deliberate exception: the fill step alone is 1.1-1.3:1); error bubble `danger-subtle` with a Secondary Try again (36px, 44px hit area); the greeting is the first bot bubble, with a "Loading…" status row while it loads and an error bubble on failure; code blocks always dark (`#0A0A0F`); sources are text links with the host, no favicons; the launcher is hidden while the panel is open (the header X and Escape close it, focus returns to the launcher); safe-area insets on phones (one layer only: the loader frame in bubble full screen, the widget in inline and standalone), the dark header runs under the status bar; desktop resize grip in the header corner opposite the launcher, hidden at rest and revealed on panel hover or focus-within, focusable with Arrow keys (20px step, Shift 80px, Home = default size; the keyboard alternative to dragging), not rendered in full screen, in inline mode or for `(pointer: coarse)` / `(hover: none)` |

Breakpoints (Tailwind defaults): sm 640, md 768, lg 1024, xl 1280. Everything except the dashboard works from 320px with no horizontal scroll; single column below 640.
Widget colour: text, links, focus and status use `brand`; the launcher, send button and visitor bubble use `accent`, flat `#7C3AED` (`#A78BFA` send and bubble in dark) unless the tenant sets `color`, then the glyph and bubble text are white or `#000000`, whichever contrasts more. Embed options: `mode, color, position, target, topClearance, theme, userHints`.

## 9. Navigation (dashboard)

Decision 3. Routes do not change; only labels, order, grouping and the settings tab strip. Sidebar `w-52` (replaces `w-[200px]`); top bar `h-12` (48px) on `chrome`, `chrome-line` bottom border, with content starting `pt-8` below it (replaces `pt-[calc(48px+32px)]`): logo (to /dashboard), account e-mail, "Log out" (44px button).

Sidebar (208px, `chrome`, `chrome-line` right border, dark in both schemes). Row = 44px, 16px lucide icon + 8px gap + label (`text-ui` 500); inactive `chrome-fg-muted`, hover `chrome-hover` + `chrome-fg`, pressed `chrome-press` + `chrome-fg` (no scale; a selected row keeps `chrome-active`), active `chrome-active` + `chrome-accent` text + 2px leading bar + `aria-current="page"`. Groups are separated by 24px of space, no group labels.

The session cookie carries the e-mail but not the role, so until `/tenants/me` answers the Settings and Platform metrics rows are reserved as empty 44px slots (nothing visible moves when they fill or collapse), and the top-bar e-mail sits in a fixed `w-60` right-aligned slot (`truncate`, full address in `title`) so the scheme trigger and Log out never change x.

Analytics (`/analytics`) is one `max-w-3xl` column (title row, period tabs, values, error, empty), so the tabs' right edge is the values' right edge.

| Order | Label | Route | Icon | Badge / notes | Visible to |
|---|---|---|---|---|---|
| 1 | Inbox | /inbox | `inbox` | `attention_count` from the inbox summary (conversations that need a person) | everyone (actions need a seat) |
| 2 | Knowledge | /knowledge | `book-open` | tabs Documents / Profile / FAQ / Unanswered; count of groups that became open since this user last opened the Unanswered tab | everyone |
| 3 | Analytics | /analytics | `chart-column` | | everyone |
| 4 | Install widget | /dashboard, active also on /embed | `code-xml` | merges Dashboard + Embed; its "Configure" button keeps the bubble/inline choice | everyone |
| 5 | Settings | /settings, active on all four tab routes | `settings` | | owner only |
| 6 | Platform metrics | /admin/metrics | `activity` | | platform admins only |

Spacing groups: 1-3 daily work | 4-5 setup | 6 staff. Operators (invited colleagues) see 1-4. Badge (`CountBadge`, section 6): Inbox is tone `brand`, Knowledge tone `quiet`; hidden at 0, while loading and on error; the pill is `aria-hidden` and a visually hidden suffix reads ", 3 need attention" / ", 3 new" / ", 3 new unanswered questions" (", 1 new unanswered question"); opening the Unanswered tab clears the Knowledge badge at once; the tab itself carries no badge; the Inbox number must equal the "Needs attention" count on its page.

Inbox (`/inbox`): two panes in one bordered `surface`. List row: name `truncate` (`title` = name and e-mail), one-line preview (plain text from the API, never markdown), meta line as a flex row: lead token (`shrink-0 whitespace-nowrap tabular-nums`: "Waiting 12 min", "Oct 18, 15:54", "Resolved Oct 18"), ticket number (`shrink-0 font-mono`), tail (`min-w-0 truncate`: "Held by anna", "Answered by email", "Closed after inactivity"); truncation only ever eats the tail and the whole line is in `title`. Thread header, three rows at most and row 2 always present: identity (name `text-body` 600 and e-mail `text-caption fg-muted`, both wrap with `break-anywhere`, then the state pill); a status line `text-ui fg-muted`, `min-h-5` kept when empty, `Held by you · {ticket} · {status} · {priority} · {trigger}` (a held chat changes text inside this line, nothing shifts; held by someone else shows the local part with the full e-mail in `title`; the ticket number stays `font-mono`); the visitor note when there is a ticket. "Answered by email" is one `singleLine` info notice, the last child of the header block (12px under its last row, above the header border): "Answered by email · {date, time} · {sender address}". The detail pane is blank when the list has no rows (the list's own empty statement is the only message); "Select a conversation." shows only when there are rows and none is selected.

Knowledge > Unanswered (`/knowledge?tab=unanswered`, filter `&show=answered|not-needed`, Open has no param): `Select` "Show" (w-56, as in FAQ), then one flat list, newest first, `divide-y line` with no cards. Row: question `text-body` 600 (wraps fully) over a `text-caption` `fg-muted` meta line joined by " · " ("12 conversations", "Last asked Oct 18", "Bot offered a human", "Answer didn't help" in `warning` text, "Draft saved", "Not needed: reason"); actions on the right, first button level with the first line of the question: Ghost link "Open conversation" (every filter), then Ghost "Not needed", Secondary "Answer" (Open); text link "View FAQ entry" (Answered); Secondary "Restore" (Not needed). "Not needed" opens a `RadioGroup` reason picker (`w-fit`, as wide as its button row) in the actions slot on the right, its buttons right-aligned so the pointer stays where it was (none preselected, Primary "Mark as not needed" disabled until a reason is chosen, Escape cancels). Past 50 rows a Secondary "Show more". States: loading (`SkeletonRegion`, 5 rows), empty per filter, `LoadError` (loaded rows stay on a failed refetch), danger `InlineNotice` for a failed row action. No highlight of new rows and no text about service state.

Answer drawer (Knowledge > Unanswered, `AnswerDrawer`): `Drawer` titled "Answer this question", body `space-y-6`, footer fixed with `border-t line`. Top: "Visitor questions" (`text-body` 600), a `QuestionList` of up to 5 recent visitor question texts from the draft's `examples` (3 shown, rest behind "Show N more questions"), caption "N conversations in the last 30 days". Below it, one of: skeleton fields while loading; `DraftProgress` (status line, elapsed seconds, skeleton fields) while the first draft is written, polled every 3 s until it settles, and closing the drawer does not cancel it; `LoadError` when the draft cannot be loaded; or the editable fields (question `Input`, Markdown answer `Textarea`, "Instructions" `Textarea` with Secondary "Ask agent"). Notices are `InlineNotice` under the examples (no OpenAI key, generation failed with "Try again", rewrite rejected, save failed, edit conflict with "Reload draft", draft reloaded with the user's previous text kept in a copyable `surface-alt` block). The drawer guards unsaved edits with `canClose` / `onCloseBlocked` and a "Discard unsaved changes?" `ConfirmDialog`. Footer: Secondary "Save draft", Primary "Publish to bot" (owner only; for operators it is `aria-disabled` and described by the caption "Only the tenant owner can publish. Your draft is kept for the owner."). Publish asks for confirmation; failures keep the dialog open with a specific message: text that reads as an instruction to the bot ("Edit it before publishing"), question already answered ("Close this panel and refresh the list"), or a failed FAQ index ("nothing was published", try again); anything else gets "Couldn't publish the answer. Try again."

Settings tab strip (SectionTabs, shared with the Knowledge tabs; see 6): Bot (/settings) | Widget (/widget-settings) | Team (/settings/members) | Seats (/settings/seats). Every tab keeps its URL, all four pages have h1 "Settings", all four are owner-only; a non-owner opening them by URL gets the one-line state from SettingsShell. Page headings follow the menu labels: Knowledge, Install widget, Platform metrics ("Per-tenant metrics" inside). "Dashboard" from now on means the whole app, never a page. Docs pages, the FAQ and the changelog use these menu terms.

## 10. Decisions

1. Colour scheme: System by default plus manual System / Light / Dark switch, remembered in a cookie, one setting for site, auth, dashboard and docs (4.1).
2. Widget theme: chosen by the tenant with the `theme` embed option (`light` default, `dark`); no `auto`, no system-setting following. Supersedes "always light" (customer decision 2026-10-17). Old snippets render light.
3. Dashboard navigation: the grouped menu of section 9.
4. Accent: the fixed violet everywhere in the product UI. In the widget the tenant `color` option (default `#7C3AED`) also drives `accent` / `accent-on`: launcher, send button and visitor bubble; the rest of the panel never takes tenant colour.
5. Logo: the mark is unchanged (`#E879F9`/`#38BDF8`). Asset requirement: a variant for light backgrounds (same shapes; mark colours darkened until each part reaches 3:1 on `canvas` and `surface`, wordmark in `fg`), selected by scheme in marketing header, auth and docs; the dashboard sidebar and top bar are always dark chrome and use the original mark (8.03 / 9.22). Not drawn yet.
6. Targets: 44px minimum everywhere, including the dashboard; density comes from 14px type and spacing, not from smaller controls.
7. Typeface: keep the bundled Geist Sans + Geist Mono for site, auth, dashboard and docs; the widget uses the system font stack (section 1).
8. 2026-10-16 chrome is dark in the light scheme: dashboard sidebar and top bar are one dark set (`chrome`, 4.2) in both schemes; in dark the working area is lifted one step above it (canvas, surface, surface-alt, line, line-strong, fg-faint; section 4) and the dark `*-subtle` fills sit above `surface`. Supersedes "the sidebar follows the scheme".
9. 2026-10-30 UI taste pass: button press push-in (scale, buttons only), heading tracking, light brand-subtle `#EDE9FE` and warning `#A15C07`, lighter Secondary outline (`line-button`), pressed state for every button and row, motion policy (120ms strong ease-out default, 200ms enter, no pulse, one reduced-motion rule), hover only where the device can hover. Supersedes "nothing scales" and "no letter-spacing" of v2. Light-scheme near-black chrome stays (decision 8).

## 11. Known product-wide fixes (token-level, for implementers)

- Every text and placeholder colour under 4.5:1 is replaced by a role: Inbox meta (2.56), placeholders (2.6-3.6), sidebar label (1.73), banner Dismiss (3.86), auth hint (3.58), docs lead text (4.33), docs accent text (~3.2), white on the docs primary (3.52).
- Field and control boundaries use `line-strong` (auth inputs are 1.23:1 today); never `line`; the one exception is the Secondary button outline (`line-button`), whose label carries the 4.5:1 identification.
- Four accents (fuchsia shell, violet-600, blue-600 visitor bubble, `#a855f7` widget) collapse to `brand`; cyan retires.
- Raw hex in auth pages (`SetPasswordForm`, verify, signup, accept-invite) and the login banners' own radius/colours move to tokens and the banner tiers in section 6.
- One font stack (Arial vs `ui-sans-serif` today); all-caps labels and untabulated numbers (StatCard, admin tables) follow section 2.
- Targets of 28-37px (Log out, copy icons, scope toggle, Dismiss, Send/Take, docs sidebar links) become 44px.
- Docs prose: the typography plugin vs fumadocs prose clash (literal backticks around inline code, underlined h2) is resolved: the dashboard build no longer registers `@tailwindcss/typography` (only the widget-app build does) and prose is styled from these tokens only.

## 12. Maintenance

Any change to a token, scale or component rule is made in this file in the same task as the code. New components not listed in section 6 are listed here with the reason before they are built.

## Widget v2 exceptions (owner-requested compactness)

Recorded as widget exceptions to section 6 (44px targets, `line-strong` boundaries, no subtitles): the input is 40px with a 1px `line` border (boundary 1.2:1, below 3:1; mitigated by the fill step, a 14px placeholder at 5.4:1 and the 2px `focus` border; `line-strong` returns if strict compliance is preferred); close, send, Copy and retry buttons keep a 44px hit area under smaller visuals (32, 36, 32, 36); the attribution line is 12px with about a 40px hit area; the header title has line-height 20 so the header stays 48; the second header line is an attribution, not an explanatory subtitle. The resize grip's 24px hit area is below 44px (pointer-only enhancement, desktop fine pointers; the same resize is available from the keyboard on the focused grip).
