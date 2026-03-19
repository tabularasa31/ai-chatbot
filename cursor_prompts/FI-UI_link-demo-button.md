# FI-UI: Link "See demo" Button to Demo Section — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
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
- `frontend/components/marketing/Hero.tsx` — Update the "See demo" button

**Do NOT touch:**
- Other components
- Marketing layout or structure
- Backend files
- migrations

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** The "See demo" button in the Hero section (landing page) currently does nothing. It should scroll the page down to the "See Chat9 in action" section (DemoBlock component).

**Current state:**
- Hero.tsx has a button with text "See demo"
- It's currently just a `<button>` tag with no href or onClick
- DemoBlock.tsx contains the "See Chat9 in action" section we want to scroll to

**What we need:**
- Make the button scroll to the DemoBlock section
- Use smooth scroll behavior
- Keep the button styling consistent

**Why this matters:** Users should be able to quickly jump to the demo from the hero section without scrolling manually.

---

## WHAT TO DO

### 1. Add an `id` to DemoBlock section

The DemoBlock component's outer `<section>` needs an `id` so we can anchor to it.

In `frontend/components/marketing/DemoBlock.tsx`, find the line:
```jsx
<section className="max-w-7xl mx-auto px-6 py-20">
```

Change it to:
```jsx
<section id="demo" className="max-w-7xl mx-auto px-6 py-20">
```

### 2. Update the "See demo" button in Hero

In `frontend/components/marketing/Hero.tsx`, find this button:
```jsx
<button className="border border-[#38BDF8] text-[#FAF5FF] px-8 py-3 rounded-lg hover:bg-[#38BDF8]/10 hover:scale-105 transition-all">
  See demo
</button>
```

Replace it with:
```jsx
<button
  onClick={() => {
    const demoSection = document.getElementById('demo');
    if (demoSection) {
      demoSection.scrollIntoView({ behavior: 'smooth' });
    }
  }}
  className="border border-[#38BDF8] text-[#FAF5FF] px-8 py-3 rounded-lg hover:bg-[#38BDF8]/10 hover:scale-105 transition-all"
>
  See demo
</button>
```

**What this does:**
- `onClick` handler uses native DOM API to find the demo section
- `scrollIntoView({ behavior: 'smooth' })` scrolls smoothly to that element
- No external dependencies needed — uses built-in browser API

---

## TESTING

Before pushing:
- [ ] Landing page loads without errors
- [ ] "See demo" button is visible in Hero section
- [ ] Clicking "See demo" scrolls smoothly to "See Chat9 in action" section
- [ ] Button styling is unchanged (still has cyan border, hover effect)
- [ ] Scroll behavior is smooth (not instant)
- [ ] No console errors

---

## GIT PUSH

```bash
git add frontend/components/marketing/Hero.tsx frontend/components/marketing/DemoBlock.tsx
git commit -m "feat: add smooth scroll to demo section from hero button (FI-UI)"
git push origin feature/link-demo-button
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- This uses the native `scrollIntoView` API — no jQuery or external libraries needed
- `behavior: 'smooth'` is supported in all modern browsers (Chrome, Firefox, Safari, Edge)
- The `document.getElementById('demo')` will not throw an error even if the element doesn't exist (we check `if (demoSection)`)
- This approach is lightweight and performant

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Added smooth scroll functionality to "See demo" button in Hero section. Button now scrolls users to the "See Chat9 in action" demo section below.

## Changes
- `frontend/components/marketing/Hero.tsx` — Updated "See demo" button with onClick handler that scrolls to demo section
- `frontend/components/marketing/DemoBlock.tsx` — Added `id="demo"` to section for scroll anchoring

## Testing
- [x] Button click triggers smooth scroll to demo section
- [x] Scroll behavior is smooth (not instant)
- [x] Button styling unchanged
- [x] No console errors
- [x] Works on desktop and mobile

## Notes
Uses native browser `scrollIntoView` API with smooth behavior. No external dependencies needed.
```
