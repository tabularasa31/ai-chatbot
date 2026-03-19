# FI-UI: Link "See demo" Button to Demo Section — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b feature/link-demo-button
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main (ensure you have latest code)
3. Create NEW branch from main (do not reuse old branches)

**DO NOT:**
- Skip `git pull origin main` — this ensures you see latest changes
- Reuse branches from previous attempts
- Assume your local main is up-to-date
- Work on any branch other than the newly created feature branch

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/marketing/Hero.tsx` — "See demo" as in-page link to `#demo`
- `frontend/components/marketing/DemoBlock.tsx` — add `id="demo"` on the outer `<section>`
- `frontend/app/globals.css` — optional smooth scrolling when the user has not requested reduced motion

**Do NOT touch:**
- Other components or marketing layout beyond the above
- Backend files
- migrations

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** The "See demo" control in the Hero section (landing page) does nothing. It should scroll the page to the "See Chat9 in action" section (`DemoBlock`).

**Current state:**
- Hero has a passive `<button>` (no action)
- `DemoBlock` wraps the demo in an outer `<section>` without an anchor id

**What we need:**
- Scroll to the demo section on click
- Smooth scroll for users who allow motion; instant jump when `prefers-reduced-motion: reduce`
- Same visual styling as today
- Prefer semantic in-page navigation: `<a href="#demo">` (works without JS, better for keyboard/AT than a fake button)

**Why this matters:** Users jump to the demo from the hero without manual scrolling.

---

## WHAT TO DO

### 1. Add an `id` to DemoBlock section

In `frontend/components/marketing/DemoBlock.tsx`, on the outer `<section>`:

```jsx
<section id="demo" className="max-w-7xl mx-auto px-6 py-20">
```

### 2. Replace "See demo" `<button>` with an anchor in Hero

In `frontend/components/marketing/Hero.tsx`:

```jsx
<a
  href="#demo"
  className="border border-[#38BDF8] text-[#FAF5FF] px-8 py-3 rounded-lg hover:bg-[#38BDF8]/10 hover:scale-105 transition-all inline-block text-center"
>
  See demo
</a>
```

(`inline-block text-center` matches the primary CTA `Link` treatment.)

### 3. Smooth scroll via CSS (respect reduced motion)

In `frontend/app/globals.css` (after `body { ... }`):

```css
@media (prefers-reduced-motion: no-preference) {
  html {
    scroll-behavior: smooth;
  }
}
```

**Why not `scrollIntoView` in JS:** Anchor + CSS keeps behavior declarative, avoids `document` in a component that might run in other contexts, and honors system motion preferences without extra logic.

---

## TESTING

Before pushing:
- [ ] Landing page loads without errors
- [ ] "See demo" is visible in Hero
- [ ] Click navigates to "See Chat9 in action" (URL shows `#demo`)
- [ ] With default OS motion settings, scroll is smooth
- [ ] With "reduce motion" enabled, scroll is immediate (no forced animation)
- [ ] Styling matches previous button (cyan border, hover)
- [ ] No console errors

---

## GIT PUSH

```bash
git add frontend/components/marketing/Hero.tsx frontend/components/marketing/DemoBlock.tsx frontend/app/globals.css
git commit -m "feat: add smooth scroll to demo section from hero (FI-UI)"
git push origin feature/link-demo-button
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- In-page links use the browser’s native scroll; `scroll-behavior: smooth` is widely supported in current Chrome, Firefox, Safari, and Edge.
- The landing page parent is already a client component; this solution does not require `'use client'` on `Hero` for the link itself.

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Linked the Hero "See demo" control to the demo section via `#demo`, with smooth scrolling when the user allows motion.

## Changes
- `frontend/components/marketing/Hero.tsx` — `<a href="#demo">` with unchanged visual styles
- `frontend/components/marketing/DemoBlock.tsx` — `id="demo"` on outer section
- `frontend/app/globals.css` — `scroll-behavior: smooth` only when `prefers-reduced-motion: no-preference`

## Testing
- [x] Click scrolls to demo section; hash `#demo` in URL
- [x] Smooth scroll with default motion preferences
- [x] Reduced motion → instant jump
- [x] Styling unchanged
- [x] No console errors

## Notes
Semantic anchor navigation; no JS scroll API required.
```
