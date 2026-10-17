# DESIGN.md — Chat9 design system (v2, 2026-10-16)

Scope: marketing site + blog, auth pages, tenant dashboard, embeddable chat widget, client docs (fumadocs).
Every screen uses only the values below; no value is invented per screen.
Site, auth, dashboard and docs support two colour schemes (light, dark), chosen as in 4.1. The widget is light only. Tokens below are scheme-independent names; each has a light and a dark value (the widget uses the light value).

Contrast figures were computed by hand with the WCAG 2.x relative-luminance formula. Re-verify with a tool when a token changes.

## 1. Typeface

- Sans: Geist Sans (variable, already in `frontend/app/fonts/GeistVF.woff`, self-hosted). Reason: neutral UI grotesque, readable at 12-14px, variable weights (one file), tabular figures, already shipped. Stack: `Geist, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`.
- Mono: Geist Mono (already in repo) for code, IDs, keys, embed snippets only. Two families total; nothing else.
- Widget: system font stack (no font download), decision 2026-10-16. Sans `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`; mono `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`. Chat content arrives in any language, which the system stack covers.
- Coverage measured with fontTools (2026-10-16) on `frontend/app/fonts`: `GeistVF.woff` has Cyrillic U+0410-044F 64/64, Latin Extended-A 117/127, Greek U+0391-03C9 only 4 of ~57 (effectively none); `GeistMonoVF.woff` has the same profile. Keep the bundled Geist files, no new dependency. Greek and any other uncovered script fall back to `system-ui` per character by design; this is accepted.
- `body` uses this stack; Arial and `font-['Inter']` are not used.

## 2. Type scale (6 sizes, 3 weights)

Weights: 400 regular (body), 500 medium (labels, buttons, nav, table headers), 600 semibold (headings only). No other weights, no letter-spacing, no all-caps.

| Token | Size / line height | Use |
|---|---|---|
| `text-caption` | 12 / 16 | meta, table headers, helper text, timestamps, badges |
| `text-ui` | 14 / 20 | default in dashboard and widget: controls, tables, nav, messages |
| `text-body` | 16 / 24 | default on marketing, blog, docs, auth; inputs use `text-body` everywhere except the dashboard (desktop only), which uses `text-ui` (14 / 20) |
| `text-h2` | 20 / 28, 600 | section heading, modal title |
| `text-h1` | 24 / 32, 600 | page title (dashboard, auth, docs page title) |
| `text-display` | 36 / 44, 600, from 768px (24/32 below) | marketing/blog page title only |

Heading map: display = marketing/blog H1; h1 = H1 everywhere else; h2 = H2; H3 and below = `text-body` 600. Headings of one level never differ between screens.
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
| `rounded-surface` | 12 | cards, modals, drawer, banners, widget panel |
| `rounded-bubble` | 16 | chat message bubbles |
| `rounded-full` | 9999 | avatars, count badges, icon buttons |

Elevation: shadows only for things that float. Cards, sections, banners, auth card, header: no shadow.

| Token | Light | Dark | Use |
|---|---|---|---|
| `shadow-pop` | `0 4px 12px rgb(15 23 42 / 0.12)` | `0 4px 12px rgb(0 0 0 / 0.5)` | dropdown, popover, tooltip |
| `shadow-overlay` | `0 16px 40px rgb(15 23 42 / 0.16)` | `0 16px 40px rgb(0 0 0 / 0.5)` | modal, drawer |
| `scrim` | `#0A0A0F` at 60% | `#0A0A0F` at 60% | behind modal, drawer |
| `chrome` (colour, `bg-chrome`) | `#0A0A0F` | `#0A0A0F` | dark plate behind artwork drawn for dark backgrounds (blog covers), both schemes |

In dark, shadows are nearly invisible: floating layers also use `surface-alt` + 1px `line`.
Motion: 120ms ease-out for colour/opacity state changes; 200ms for modal/drawer enter. Nothing scales, bounces or loops decoratively (remove `hover:scale-105`). Honour `prefers-reduced-motion` (no transition).

## 4. Colour roles

One accent (violet). Neutrals are Tailwind slate in light and the existing `nd` dark neutrals in dark. Semantic colours only for their meaning. No gradients. Text colour is never expressed as opacity of another colour (`text-nd-text/50`); use the role.
Token names avoid `accent`, `primary`, `foreground`, `card` because the fumadocs preset already defines them (its `bg-accent` is a hover background and would turn solid violet).

### Light

| Role (Tailwind name) | Hex | Contrast |
|---|---|---|
| `canvas` page background | `#F8F9FA` | - |
| `surface` cards, inputs, modals, tables | `#FFFFFF` | - |
| `surface-alt` hover rows, table header, code, assistant bubble, skeleton | `#F1F5F9` | - |
| `line` dividers, card border (decorative, exempt) | `#E2E8F0` | - |
| `line-strong` control boundaries (input, select, checkbox, secondary button) | `#7C8BA1` | 3.46 on surface, 3.29 on canvas, 3.16 on surface-alt |
| `fg` primary text | `#1E293B` | 14.62 surface, 13.88 canvas, 13.35 surface-alt |
| `fg-muted` secondary text | `#475569` | 7.58 surface, 7.19 canvas, 6.92 surface-alt |
| `fg-faint` placeholder, disabled, hints | `#586579` | 5.91 surface, 5.61 canvas, 5.40 surface-alt |
| `brand` accent: primary button fill, links, selected, focus | `#7C3AED` | 5.70 surface, 5.41 canvas, 5.20 surface-alt |
| `brand-hover` | `#6D28D9` | white on it 7.10 |
| `brand-active` | `#5B21B6` | white on it 8.99 |
| `brand-on` text on brand | `#FFFFFF` | 5.70 on brand |
| `brand-subtle` selected row/nav bg | `#F5F3FF` | brand text on it 5.20 |
| `focus` ring | `#7C3AED` (= brand) | 5.70 surface, 5.41 canvas (needs 3.0) |
| `success` / `success-subtle` | `#15803D` / `#F0FDF4` | 5.02 surface, 4.76 canvas, 4.58 surface-alt, 4.79 on subtle |
| `warning` / `warning-subtle` | `#B45309` / `#FFFBEB` | 5.02 surface, 4.77 canvas, 4.58 surface-alt, 4.84 on subtle |
| `danger` / `danger-subtle` | `#B91C1C` / `#FEF2F2` | 6.47 surface, 6.14 canvas, 5.91 on subtle |
| `danger-hover` | `#991B1B` | white on it 8.31 |
| `danger-on` | `#FFFFFF` | 6.47 on danger |
| `info` / `info-subtle` | `#0369A1` / `#F0F9FF` | 5.93 surface, 5.63 canvas, 5.56 on subtle |

### Dark

| Role | Hex | Contrast |
|---|---|---|
| `canvas` | `#0A0A0F` (was nd-base) | - |
| `surface` | `#12121A` (was nd-base-alt) | - |
| `surface-alt` | `#1E1E2E` (was nd-surface) | - |
| `line` (decorative, exempt) | `#2E2E3E` (was nd-border) | - |
| `line-strong` | `#716C87` | 3.72 surface, 3.95 canvas, 3.28 surface-alt |
| `fg` | `#FAF5FF` (was nd-text) | 17.36 surface, 18.41 canvas, 15.28 surface-alt |
| `fg-muted` | `#A9A3BA` | 7.66 surface, 8.12 canvas, 6.74 surface-alt |
| `fg-faint` | `#8A849C` | 5.20 surface, 5.51 canvas, 4.58 surface-alt |
| `brand` | `#A78BFA` | 6.85 surface, 7.26 canvas, 6.03 surface-alt |
| `brand-hover` | `#C4B5FD` | brand-on on it 10.70 |
| `brand-active` | `#8B5CF6` | brand-on on it 4.66 |
| `brand-on` | `#0A0A0F` | 7.26 on brand |
| `brand-subtle` | `#231C3F` | brand text on it 5.90 |
| `focus` | `#A78BFA` | 6.85 surface, 7.26 canvas |
| `success` / `success-subtle` | `#4ADE80` / `#11271B` | 10.69 surface, 11.33 canvas, 9.06 on subtle |
| `warning` / `warning-subtle` | `#FBBF24` / `#2B2112` | 11.16 surface, 11.83 canvas, 9.47 on subtle |
| `danger` / `danger-subtle` | `#F87171` / `#2D1517` | 6.74 surface, 7.15 canvas, 6.17 on subtle |
| `danger-hover` | `#FCA5A5` | danger-on on it 10.40 |
| `danger-on` | `#0A0A0F` | 7.15 on danger |
| `info` / `info-subtle` | `#38BDF8` / `#10222F` | 8.70 surface, 9.22 canvas, 7.59 on subtle |

In dark, `fg-faint` is not used on `brand-subtle` (4.48:1); use `fg-muted` (6.59:1).

Contrast changes made to the existing look:
- Accent: the product used magenta `#E879F9` (marketing, auth, sidebar) and violet-500/600 (dashboard, widget). Magenta on a light background is ~2.4:1 and fails. The single accent is violet: `#7C3AED` light, `#A78BFA` dark. The logo asset keeps its own `#E879F9`/`#38BDF8` (brand mark, not UI); see Decisions.
- Cyan (`#67E8F9`, `#5EC8FF`) link/tag colours on blog/docs are retired: links use `brand`; one accent only.
- `slate-400` text (labels, ~2.56 on white) and `gray-400` text in the widget (~2.5) fail 4.5 and are replaced by `fg-faint`.
- White text on `amber-600` buttons (~3.2) fails; warning is never a button fill.
- Disabled controls: `surface-alt` fill + `fg-faint` text, no opacity trick. Contrast-exempt by WCAG, but still readable.

### 4.1 Scheme selection (site, auth, dashboard, docs)

- One shared setting with three values: System (default), Light, Dark. System follows `prefers-color-scheme`; if the browser gives no signal, light.
- Stored in one cookie `chat9-scheme` (`system|light|dark`; `Path=/`, 1 year, `SameSite=Lax`, readable by script because the switch writes it). Marketing, auth, dashboard and docs read the same cookie, so the choice carries across them; site, auth, dashboard and docs are one Next.js app on one host, so a host-only cookie (no `Domain`) is enough.
- No flash: the server renders no scheme class, so pages stay statically cacheable. A tiny blocking script in the root layout's `<head>` reads the cookie, resolves System through `matchMedia`, and sets `class="dark"` on `<html>` before first paint; it re-resolves when the OS preference or the cookie changes (the switch dispatches `chat9-scheme-change`). `<html>` carries `suppressHydrationWarning`. A surface that forces its own scheme (docs, until it is migrated) renders `[data-scheme-forced]`; while that element is mounted the script does not touch `<html>`. A migrated surface calls `resolveScheme()` (`frontend/lib/scheme.ts`) when it mounts, so client-side navigation from a forcing surface restores the stored scheme. A surface that has migrated to tokens renders `data-scheme-surface`, which sets `color-scheme` to match the resolved scheme so native controls and scrollbars follow.
- Docs: remove `forcedTheme: light` and fumadocs' own toggle; fumadocs reads the same class (no second storage key, no second control).
- Switch control (one component, everywhere): segmented radio group of three 44x44 icon buttons, lucide `Monitor` (System), `Sun` (Light), `Moon` (Dark), 16px, `aria-label` and tooltip with the word, selected = `brand-subtle` fill + `brand` icon, `role="radiogroup"`. Arrow keys move, selection applies immediately, no save step.
- Placement, one per surface: dashboard = foot of the sidebar; marketing + blog = footer, right end of the bottom row; auth = below the card, left edge aligned to the card; docs = foot of the docs sidebar (inside the mobile drawer on narrow widths). Not in any header, not in the widget.

## 5. Tailwind mapping

Implementation shape: CSS variables (`--c-canvas` ... as space-separated RGB) on `:root` for light and on `.dark` for dark (not in the widget build); `tailwind.config.ts` `darkMode: "class"`; colours declared as `rgb(var(--c-x) / <alpha-value>)` in the shared `tailwind-brand.ts` so dashboard and widget-app builds stay in sync. The fumadocs preset already switches on the `.dark` class: feed its `light` and `dark` slots with the two scheme values (derive HSL from the hex, do not hand-maintain). The `nd-*` palette is not used.

Fumadocs variable -> token: `background`=canvas, `foreground`=fg, `muted`=surface-alt, `muted-foreground`=fg-muted, `card`/`popover`=surface, `border`=line, `primary`=brand, `primary-foreground`=brand-on, `secondary`=surface-alt, `accent`=brand-subtle, `accent-foreground`=fg, `ring`=focus.

Names: `bg-canvas`, `bg-surface`, `bg-surface-alt`, `border-line`, `border-line-strong`, `text-fg`, `text-fg-muted`, `text-fg-faint`, `bg-brand hover:bg-brand-hover active:bg-brand-active text-brand-on`, `bg-brand-subtle`, `ring-focus`, `text-success bg-success-subtle` (same for warning, danger, info), `bg-danger text-danger-on hover:bg-danger-hover`, `bg-scrim`. Sizes: `text-caption|ui|body|h2|h1|display`. Also `shadow-pop`, `shadow-overlay`, `rounded-tag|control|surface|bubble`. Control height `h-11` (44px).

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
| `nd-base #0A0A0F`, `nd-base-alt #12121A`, `nd-surface #1E1E2E`, `nd-border #2E2E3E`, `border-white/[0.07]` | `canvas`, `surface`, `surface-alt`, `line`, `line` (dark values) |
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
| `hover:scale-105`, `transition-all` | colour transition only |

## 6. Components (only those in the product today)

General: every interactive element has hover, active, focus-visible, disabled; hit area at least 44x44 (`h-11`; icon buttons `h-11 w-11`, glyph 20px). Inline text links inside prose are exempt. No action is available only on hover; row actions are always visible. One primary action per screen (or per modal/form section).

Focus: `focus-visible` only, 2px `focus` ring with 2px offset in the underlying surface colour; never `outline-none` without this ring. Hover: one colour step (never scale/translate). Active: `brand-active`. Disabled: see 4.

Buttons (`ui/button.tsx`: `Button` for `<button>`, `buttonClasses` for links that look like buttons; one definition of height 44, radius, focus ring and the variants below, so no component copies the class strings; `offset` names the surface the ring offset matches). 14/20, 500, `px-4`, `rounded-control`, label is a verb describing the result: "Save widget settings", not "Submit":
- Primary: `brand` fill, `brand-on` text. Link CTAs (`<a>`) look identical.
- Secondary: `surface` fill, 1px `line-strong`, `fg` text; hover `surface-alt`. Cancel, Copy, Back.
- Destructive (added to `ui/button.tsx` with its first caller): `danger` fill, `danger-on` text. Only in the confirm step of a destructive flow, with the consequence stated next to it. Never the default focus in a modal.
- Ghost (icon buttons only, `size="icon"` = 44x44): no fill, `fg-muted` glyph, hover `surface-alt`. Dismiss, close.
- Loading: label stays, 16px spinner (lucide `Loader2`, only rotating element allowed) before it, width unchanged, `aria-busy`, clicks ignored.
- Order in a row: secondary left, primary right; on the auth card the primary is full-width.

Text input and select: 44px high, `px-3`, `surface` fill, 1px `line-strong`, `rounded-control`, `fg` text, `fg-faint` placeholder. Label above (14/20, 500, `fg`), never placeholder-as-label. Inputs use `text-body` (16/24) everywhere except the dashboard (desktop only), which uses `text-ui` (14/20). Select is the native `<select>` with a 16px lucide chevron. Textarea same. Error: border `danger`, message below in `danger` 14/20 linked by `aria-describedby`; the user's input is kept. Helper text (12/16 `fg-muted`) only for a format, limit or non-obvious rule. Checkbox/toggle: 20px control, whole label row is the 44px target, `brand` when on.

Card: only for a standalone, actionable object (a source, a document, an inbox conversation): `surface` + 1px `line`, `rounded-surface`, `p-4`, no shadow. Never inside another card. Page sections (settings, forms) are not cards: heading, spacing, `line` divider. The auth form container is the one allowed container. StatCard-style metric tiles exist today; use only where a number is the object the user acts on, otherwise a plain labelled value.

Count badge (`ui/count-badge.tsx`, new): number pill in a nav row. `rounded-full`, `h-5 min-w-5 px-1.5`, `text-caption` 500, `tabular-nums`; above 99 shows `99+`; renders nothing at 0 (also while loading or on error); a visually hidden suffix carries the meaning ("3 need attention"). Tone `brand` (`brand` fill, `brand-on` text) only for what needs a person; tone `quiet` (`surface-alt` fill, `fg` text) for the rest. Reason: the sidebar shows it twice and the badge rule (one count, one definition) must live in one place.

Modal and drawer: `surface`, `rounded-surface`, `shadow-overlay`, `scrim`. Modal `max-w-md`, `p-6`, title `text-h2`, only for a decision or a consequence that must be confirmed (link-safety confirm, destructive confirm). Drawer (document preview) from the right, `max-w-xl`, full height, same chrome. Both: focus moves in, is trapped, returns to the trigger on close; Escape and a visible close control (lucide `X`, 44px) close; the scrim click closes unless there is unsaved input. Buttons bottom-right per button order.

Table: header `text-caption` 500 `fg-muted`, sentence case, 1px `line` below; rows separated by 1px `line`, no zebra, no per-cell borders; hover `surface-alt`; row height 44 when the row has controls, 40 otherwise; cell padding `px-4`. Numbers and dates `tabular-nums`, right-aligned with their header. Long text wraps in the first column; IDs/keys in Geist Mono and truncated with the full value in a tooltip. Empty, loading, error render inside the table region (below).

Banners and notices, by urgency (only the highest tier gets a prominent treatment):
1. Page banner (top of `<main>`): only for a condition that blocks the product and that the tenant can fix (e.g. their own key invalid). `danger-subtle` or `warning-subtle` bg, 16px lucide icon in the semantic colour, `fg` text (14/20), one action link/button, `role="alert"`, 1px semantic border. At most one visible; highest severity wins. No banners for our own infrastructure degradation (infrastructure monitoring stays internal).
2. Inline notice (in a form/section): `*-subtle` bg, icon, `fg` text, no border, `role="status"`. Info and success use this tier.
3. Confirmation of a completed action ("Saved"): plain `success` text with a check icon next to the button, `aria-live="polite"`, disappears after 4s. No toast component exists; none is added.
Field errors: see input.

Page banner (`ui/page-banner.tsx`, new, section 5 tiers 1 and 2): one row at the top of `<main>`; `danger-subtle` or `warning-subtle` fill, 1px semantic border, 16px `TriangleAlert`, bold lead sentence plus one sentence in `fg` 14/20, optional Secondary action (44px; `ExternalLink` icon and `rel="noopener noreferrer"` when it leaves the app). `role="alert"` for danger, `role="status"` for warning. Reason: replaces three hand-built banners (OpenAI alert, e-mail strip above the navbar, identity banner) so urgency is defined once; blocking conditions (key invalid, quota exhausted, key not set) get the prominent treatment, the e-mail warning is quieter and shows only when nothing blocks. Roles without the fix (operators) see the same text without the action.

Inline notice (`ui/inline-notice.tsx`, new, section 5 tier 3 and confirmations): `info-subtle` fill, no border, 16px `Info`, `fg` text with text links, `role="status"`, optional 44px `X` dismiss (`aria-label` "Dismiss"). Reason: announcements and confirmations must never use alert styling; this is the single quiet notice. Dismissal of the widget identity announcement is remembered in the browser's `localStorage` (`chat9:widget-identity-banner-dismissed`), no expiry.

Load error (`ui/load-error.tsx`): `danger-subtle` fill, no border, `TriangleAlert`, `fg` message, Secondary "Try again" (44px, spinner before the label while retrying), `role="alert"`; for a region whose data failed to load. Without an `onRetry` callback the button is omitted (form-level errors on the auth pages, where retrying means submitting again).

Auth field (`components/auth/AuthField.tsx`, new): label above, 44px input as in the text input rule, helper (`text-caption` `fg-muted`) that an error (`text-ui` `danger`, `danger` border) replaces. Wires `htmlFor`, `aria-invalid` and `aria-describedby` (error id, else helper id). Reason: the same wiring repeats in nine inputs on five auth pages and its absence caused the missing aria and the 1.23:1 field border; the dashboard has no field component yet and may adopt it.

SectionTabs (`components/ui/section-tabs.tsx`): the one tab strip, replacing the Knowledge pill group so Knowledge and Settings look and behave alike. Row of 44px tabs, `gap-1`, `text-ui` 500, `rounded-control`; selected `brand-subtle` fill + `brand` text, others `fg-muted` with `surface-alt` hover, `focus` ring. No bordered outer box and no solid `brand` fill, so a selected tab never competes with the page's primary button. Two modes: links (`href` per tab, `<nav aria-label>`, `aria-current="page"`) when each tab is a URL; buttons (`onChange`, `role="tablist"`, `aria-selected`, roving tabindex, Left/Right/Home/End) when tabs switch content in place. No icons on tabs. A tab may carry an optional `suffix` node (the Inbox "Needs attention" tab shows the same count badge as the sidebar).

Markdown message (`ui/markdown-message.tsx`, new): renders assistant and operator messages in the dashboard with the widget's markdown subset (`react-markdown` + `remark-gfm`: paragraphs, bold/italic/strike, lists, links, inline code, fenced code, GFM tables; images and raw HTML are not rendered). Block gap 8px; links `brand` underlined, open in a new tab with `rel="noopener noreferrer nofollow"` and no link-safety confirm; inline code and code blocks use a `surface` fill with 1px `line` (`rounded-tag` / `rounded-control`, `p-3`, horizontal scroll), no syntax highlighting; tables `text-caption` with `line` rules inside an `overflow-x-auto` wrapper. Reason: the widget's renderer lives in the widget build with light-only colours and a dark `slate-900` code block, which breaks in the dark scheme; the dashboard needs one token-styled renderer. Visitor messages stay plain text.

SettingsShell (`app/(app)/settings/_components/SettingsShell.tsx`): H1 "Settings" (`text-h1`), 16px below it the SectionTabs strip (Bot | Widget | Team | Seats), 24px below that the tab content, in a left-aligned `max-w-2xl` column. Needed because the four tabs live in two route trees (`/settings/**`, `/widget-settings`) that a single layout cannot cover, and so the strip and H1 stay on screen while a tab loads or fails. It also holds the owner gate: a non-owner sees the H1, "Only the account owner can open Settings." and a "Back to Inbox" link, with no tabs and without mounting the tab content (no owner-only requests fire). Tab content starts at `text-h2`; a tab name is never repeated as an H1.

Marketing shell (`components/marketing/`, new): `MarketingShell` wraps every site and blog page (opts into the scheme, skip link, header, `<main id="content">`, footer); `SiteHeader` is static, 64px, text links Docs / Blog / Sign in (Dashboard when signed in) with a 44px menu button below 768 (`aria-expanded`, `aria-controls`, Escape closes and returns focus); `SiteFooter` carries the links and the one `ThemeSwitch` of the site. Reason: the old Navigation was sticky with blur, had 28px targets and no skip link; nothing shared fit.

DefinitionRows: `<ol>`/`<ul>` of rows, title (`text-body` 600) in columns 1-4 and text in 5-12 from 768, stacked below, `divide-y line`, `py-6`. Used for setup steps and conditions on the landing page. Reason: replaces the identical-card feature grid with rows whose content differs.

CodeSnippet: `surface-alt` block, Geist Mono `text-ui`, horizontal scroll inside itself (focusable, `aria-label`), secondary Copy button via `ui/copy-button`. Reason: `CodeBlockWithCopy` is dashboard-only (primary button, no scroll focus).

DemoSlot: bordered `surface` box (`h-demo` = 480 below 640, 560 from 640) holding the inline widget; skeleton after 200ms, error with Reload page after a failed or 10s-late loader; not rendered when no bot id is configured. Reason: the old block swallowed loader errors and showed an environment variable name to visitors.

BlogPostRow / BlogPostList: one row per post, meta line, title as the only link (stretched `::after`), excerpt, `divide-y line`, hover `surface-alt`. Tags are not shown. Reason: replaces the three-column card grid with a card, tags and a duplicate "Read article" link per post.

States for any data region (list, table, document list, inbox, analytics):
- Loading: nothing for the first 200ms, then skeleton blocks of the final layout in `surface-alt`, no shimmer; `aria-busy`. A whole-page spinner is not used; inline `Loader2` only for buttons and refetch.
- Empty: left-aligned statement of what is missing in plain words (`text-ui`, `fg-muted`) plus the one action that fixes it, if it exists. No illustration, no dashed box. Centering only when the empty region is the whole page body.
- Error: in place of the data, say what failed and what the user can do, plus a secondary "Try again" button; keep already-loaded data visible when a refetch fails.
- Success: the result itself plus the confirmation text above; no celebratory states.

## 7. Icons

lucide-react only (already used in dashboard and widget). Sizes 16 (inline with 14px text, nav, banners) and 20 (icon-only buttons, send/close); stroke 2 (library default); colour inherits text colour. Allowed for: sidebar navigation, standard actions (copy, close, send, delete, external link, retry, chevron), banner/status severity. Not next to every heading, field or list item. Icon-only buttons need `aria-label` and a tooltip (existing `ui/tooltip`). No emoji as icon or decoration, no avatar-with-gradient decoration. The 15px hand-drawn SVGs in the sidebar and the inline external-link SVG in the widget are replaced by lucide equivalents.

## 8. Surfaces

| Surface | Density | Text | Content width | Notes |
|---|---|---|---|---|
| Dashboard | compact | `text-ui` 14/20, titles `text-h1` | content `max-w-6xl` (1152), forms/settings column `max-w-2xl` (672) | navbar 48px, sidebar 208px, section gap 32, in-section gap 16; desktop only (from 1024px), narrower widths not designed yet. Sidebar follows the active scheme; menu: section 9 |
| Auth | comfortable | `text-body` | card `max-w-md` (448) | left-aligned form; centering only for short success/confirmation screens |
| Marketing + blog | airy | `text-body`, `text-display` | container `max-w-6xl`, prose `max-w-measure` | layout, sections and copy of the landing are decided by the later redesign under the CoC; this system supplies tokens only. No hero/feature-grid defaults |
| Docs | reading | `text-body` | fumadocs layout, prose 65ch | fumadocs preset fed from this file |
| Widget | compact | `text-ui`, input 16/24 | fills the iframe | always light (light token values only, ignores the scheme cookie and the visitor's system setting), no snippet option; user bubble `brand-subtle`, assistant `surface-alt`, operator bubble `brand-subtle` + 1px `line` and a "Operator" caption (distinct from the bot by design), error bubble `danger-subtle`; send/close targets 44px; header is `surface` with a `line` divider |

Breakpoints (Tailwind defaults): sm 640, md 768, lg 1024, xl 1280. Everything except the dashboard works from 320px with no horizontal scroll; single column below 640.
Widget colour: the fixed violet `brand` (light values); tenants cannot set a colour and no option is planned (Decision 4). Embed options stay `position` and `locale`.

## 9. Navigation (dashboard)

Decision 3. Routes do not change; only labels, order, grouping and the settings tab strip. Sidebar `w-52` (replaces `w-[200px]`); top bar `h-12` (48px) with content starting `pt-8` below it (replaces `pt-[calc(48px+32px)]`): logo (to /dashboard), account e-mail, "Log out" (44px button).

Sidebar (208px, follows the scheme). Row = 44px, 16px lucide icon + 8px gap + label (`text-ui` 500); inactive `fg-muted`, hover `surface-alt`, active `brand-subtle` fill + `brand` text + `aria-current="page"`. Groups are separated by 24px of space, no group labels. Scheme switch at the foot (4.1).

| Order | Label | Route | Icon | Badge / notes | Visible to |
|---|---|---|---|---|---|
| 1 | Inbox | /inbox | `inbox` | `attention_count` from the inbox summary (conversations that need a person) | everyone (actions need a seat) |
| 2 | Doc gaps | /gap-analyzer | `file-search` | count of new gaps | everyone |
| 3 | Knowledge | /knowledge | `book-open` | tabs Documents / Profile / FAQ | everyone |
| 4 | Analytics | /analytics | `chart-column` | | everyone |
| 5 | Install widget | /dashboard, active also on /embed | `code-xml` | merges Dashboard + Embed; its "Configure" button keeps the bubble/inline choice | everyone |
| 6 | Settings | /settings, active on all four tab routes | `settings` | | owner only |
| 7 | Platform metrics | /admin/metrics | `activity` | | platform admins only |

Spacing groups: 1-4 daily work | 5-6 setup | 7 staff. Operators (invited colleagues) see 1-5. Badge (`CountBadge`, section 6): Inbox is tone `brand`, Doc gaps tone `quiet`; hidden at 0; the pill is `aria-hidden` and a visually hidden suffix reads ", 3 need attention" / ", 3 new"; the Inbox number must equal the "Needs attention" count on its page.

Settings tab strip (SectionTabs, shared with the Knowledge tabs; see 6): Bot (/settings) | Widget (/widget-settings) | Team (/settings/members) | Seats (/settings/seats). Every tab keeps its URL, all four pages have h1 "Settings", all four are owner-only; a non-owner opening them by URL gets the one-line state from SettingsShell. Page headings follow the menu labels: Knowledge, Doc gaps, Install widget, Platform metrics ("Per-tenant metrics" inside). "Dashboard" from now on means the whole app, never a page. Docs pages, the FAQ and the changelog use these menu terms.

## 10. Decisions

1. Colour scheme: System by default plus manual System / Light / Dark switch, remembered in a cookie, one setting for site, auth, dashboard and docs (4.1).
2. Widget: always light; no dark tokens, no snippet option.
3. Dashboard navigation: the grouped menu of section 9, with "Gap Analyzer" named "Doc gaps".
4. No tenant brand colour: the accent is the fixed violet everywhere, including the widget; nothing is built for an override. The sidebar follows the scheme (no always-dark block).
5. Logo: the mark is unchanged (`#E879F9`/`#38BDF8`). Asset requirement: a variant for light backgrounds (same shapes; mark colours darkened until each part reaches 3:1 on `canvas` and `surface`, wordmark in `fg`), selected by scheme in header, auth, docs and sidebar. Not drawn yet.
6. Targets: 44px minimum everywhere, including the dashboard; density comes from 14px type and spacing, not from smaller controls.
7. Typeface: keep the bundled Geist Sans + Geist Mono for site, auth, dashboard and docs; the widget uses the system font stack (section 1).

## 11. Known product-wide fixes (token-level, for implementers)

- Every text and placeholder colour under 4.5:1 is replaced by a role: Inbox meta (2.56), placeholders (2.6-3.6), sidebar label (1.73), banner Dismiss (3.86), auth hint (3.58), docs lead text (4.33), docs accent text (~3.2), white on the docs primary (3.52).
- Field and control boundaries use `line-strong` (auth inputs are 1.23:1 today); never `line`.
- Four accents (fuchsia shell, violet-600, blue-600 visitor bubble, `#a855f7` widget) collapse to `brand`; cyan retires.
- Raw hex in auth pages (`SetPasswordForm`, verify, signup, accept-invite) and the login banners' own radius/colours move to tokens and the banner tiers in section 6.
- One font stack (Arial vs `ui-sans-serif` today); all-caps labels and untabulated numbers (StatCard, admin tables) follow section 2.
- Targets of 28-37px (Log out, copy icons, scope toggle, Dismiss, Send/Take, docs sidebar links) become 44px.
- Docs prose: the typography plugin vs fumadocs prose clash (literal backticks around inline code, underlined h2) is resolved by styling prose from these tokens only.

## 12. Maintenance

Any change to a token, scale or component rule is made in this file in the same task as the code. New components not listed in section 6 are listed here with the reason before they are built.
