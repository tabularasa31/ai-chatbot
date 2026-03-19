# FI-035: Wire CTA Buttons to Signup

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/FI-035-wire-cta
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/marketing/Hero.tsx`
- `frontend/components/marketing/CTABanner.tsx`
- `frontend/components/marketing/Navigation.tsx`
- Any other marketing components with CTA buttons

**Do NOT touch:**
- Backend, auth, database
- Other components outside marketing/

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

Landing page is live at getchat9.live/, but CTA buttons ("Try for free") are not wired up yet.

**Goal:** All "Try for free" buttons should navigate to `/signup` (auth flow).

---

## WHAT TO DO

### 1. Update All "Try for free" Buttons

Find all buttons with "Try for free" text in:
- `Hero.tsx`
- `CTABanner.tsx`
- `Navigation.tsx`

**Current (non-functional):**
```tsx
<button className="...">
  Try for free
</button>
```

**Updated (with Link):**
```tsx
import Link from 'next/link';

<Link href="/signup">
  <button className="...">
    Try for free
  </button>
</Link>
```

Or simpler, use anchor:
```tsx
<a href="/signup" className="inline-block">
  <button className="...">
    Try for free
  </button>
</a>
```

Or convert button to Link directly:
```tsx
import Link from 'next/link';

<Link 
  href="/signup" 
  className="bg-[#E879F9] text-[#0A0A0F] px-12 py-4 rounded-lg text-lg hover:bg-[#f099fb] hover:scale-105 transition-all inline-block"
>
  Try for free
</Link>
```

### 2. Check Footer Links

In `Footer.tsx`, verify:
- "Docs" link → `/docs` or external docs URL
- "GitHub" link → https://github.com/tabularasa31/ai-chatbot

Update if needed.

### 3. Update Navigation "Try for free"

In `Navigation.tsx`:
- The CTA button in header should also link to `/signup`

---

## TESTING

Before pushing:
- [ ] Click "Try for free" button on Hero → redirects to `/signup`
- [ ] Click "Try for free" button on CTABanner → redirects to `/signup`
- [ ] Click "Try for free" button in Navigation → redirects to `/signup`
- [ ] All buttons are clickable and styled correctly
- [ ] No console errors about missing routes

---

## GIT PUSH

```bash
git add frontend/components/marketing/
git commit -m "feat: wire CTA buttons to signup flow (FI-035)"
git push origin feature/FI-035-wire-cta
```

Then create PR, review, and merge.

---

## NOTES

- `/signup` route already exists in the app (auth flow)
- Use Next.js `Link` or regular `<a>` — both work for internal routes
- Make sure styling is consistent across all buttons
