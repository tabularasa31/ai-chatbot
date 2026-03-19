## Summary

Linked the Hero **See demo** control to the on-page demo section (`#demo`). Scrolling is smooth when the user allows motion, and instant when **Reduce motion** is enabled.

## Changes

- `frontend/components/marketing/Hero.tsx` — replace passive button with `<a href="#demo">`, same visual styles (`inline-block text-center` aligned with primary CTA).
- `frontend/components/marketing/DemoBlock.tsx` — add `id="demo"` on the outer `<section>` for the anchor target.
- `frontend/app/globals.css` — `scroll-behavior: smooth` on `html` only inside `@media (prefers-reduced-motion: no-preference)`.
- `cursor_prompts/FI-UI_link-demo-button.md` — prompt updated to match implementation (scope, anchor + CSS, testing, git add list).

## Testing

- [x] Landing loads; **See demo** visible in Hero
- [x] Click jumps to “See Chat9 in action”; URL shows `#demo`
- [x] Smooth scroll with default motion settings
- [x] With OS “reduce motion”, jump is not animated
- [x] Styling unchanged (cyan border, hover)
- [x] `npm run lint` (frontend) — clean

## Notes

Uses native in-page navigation plus CSS scroll behavior; no `scrollIntoView` / `document` API. Semantic `<a>` improves keyboard and assistive-tech expectations vs a non-functional button.
