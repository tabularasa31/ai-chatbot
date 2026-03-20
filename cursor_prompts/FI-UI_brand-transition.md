# FI-UI: Auth Transition + Brand Navbar

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/ui-brand-transition
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
- `frontend/app/(auth)/login/page.tsx`
- `frontend/app/(app)/layout.tsx`
- `frontend/components/Navbar.tsx`
- `frontend/components/AuthTransition.tsx` ← NEW file

**Do NOT touch:**
- `frontend/app/(marketing)/` — landing page is off-limits
- `frontend/components/marketing/` — marketing components are off-limits
- Backend files
- migrations
- Any other frontend pages

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

The landing page uses a dark neon design (`#0A0A0F` background, `#E879F9` pink accents).
After login, the user lands on the dashboard which is fully light (`bg-slate-50`, white navbar).
This feels like jumping into a different product — the visual disconnect breaks continuity.

We need two fixes:
1. **Auth Transition** — a fade-out overlay after login so the switch feels intentional, not jarring
2. **Brand Navbar** — replace the light Navbar with a dark one that matches the landing page brand

---

## WHAT TO DO

### Part 1 — AuthTransition component

Create `frontend/components/AuthTransition.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

interface AuthTransitionProps {
  onComplete: () => void;
}

export function AuthTransition({ onComplete }: AuthTransitionProps) {
  const [opacity, setOpacity] = useState(1);

  useEffect(() => {
    // Start fade-out immediately
    const fadeTimer = setTimeout(() => {
      setOpacity(0);
    }, 50); // tiny delay to ensure initial render at opacity 1

    // Call onComplete after animation finishes (400ms)
    const completeTimer = setTimeout(() => {
      onComplete();
    }, 450);

    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(completeTimer);
    };
  }, [onComplete]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "#0A0A0F",
        opacity,
        transition: "opacity 400ms ease-out",
        zIndex: 9999,
        pointerEvents: "none",
      }}
    />
  );
}
```

### Part 2 — Trigger AuthTransition in login page

In `frontend/app/(auth)/login/page.tsx`:

- Add state: `const [transitioning, setTransitioning] = useState(false);`
- On successful login (after `saveToken(token)`), instead of `router.replace("/dashboard")`:
  ```tsx
  setTransitioning(true);
  // router.replace happens inside AuthTransition's onComplete
  ```
- Render `<AuthTransition>` conditionally:
  ```tsx
  {transitioning && (
    <AuthTransition onComplete={() => router.replace("/dashboard")} />
  )}
  ```
- Keep existing JSX (the form) unchanged — the overlay renders on top

### Part 3 — Replace Navbar with brand navbar

Replace the entire content of `frontend/components/Navbar.tsx` with a new dark brand navbar:

Requirements:
- Background: `#0A0A0F`
- Height: `48px` (use `h-12`)
- Full width
- Left side: "Chat9" text logo in white (`text-[#FAF5FF]`), same style as landing Navigation — link to `/dashboard`
- Right side: user email + logout button
  - Fetch user email from `api.clients.getMe()` (already done in existing Navbar, keep that logic)
  - Email shown as small text `text-[#FAF5FF]/70 text-sm`
  - Logout button: ghost style — `text-[#E879F9] text-sm font-medium hover:text-[#E879F9]/80`, no background, no border
- Keep existing nav links (Dashboard, Documents, Logs, Review, Debug, Admin) — style them as `text-[#FAF5FF]/70 hover:text-[#FAF5FF] text-sm`
- Keep the email verification warning banner (amber strip) — keep it exactly as-is, above the nav bar

**New Navbar structure:**
```tsx
<nav>
  {/* Verification warning — keep as-is */}
  {isVerified === false && (...)}
  
  {/* Brand bar */}
  <div style={{ backgroundColor: "#0A0A0F" }} className="w-full">
    <div className="max-w-4xl mx-auto px-4">
      <div className="flex items-center justify-between h-12">
        {/* Left: logo + nav links */}
        <div className="flex items-center gap-6">
          <Link href="/dashboard" className="text-[#FAF5FF] font-semibold">
            Chat9
          </Link>
          {/* nav links */}
        </div>
        {/* Right: email + logout */}
        <div className="flex items-center gap-4">
          {userEmail && <span className="text-[#FAF5FF]/70 text-sm">{userEmail}</span>}
          <button onClick={handleLogout} className="text-[#E879F9] text-sm font-medium hover:text-[#E879F9]/80">
            Logout
          </button>
        </div>
      </div>
    </div>
  </div>
</nav>
```

- Add `const [userEmail, setUserEmail] = useState<string | null>(null);`
- In the `getMe()` call, also set `setUserEmail(c.email)`

### Part 4 — Dashboard layout background

In `frontend/app/(app)/layout.tsx`, change:
```tsx
// FROM:
<div className="min-h-screen bg-slate-50">

// TO:
<div className="min-h-screen bg-[#F8F9FA]">
```

(This keeps `#F8F9FA` as specified — just making it explicit.)

No other changes to layout.tsx.

---

## TESTING

Before pushing:
- [ ] Login flow: after entering credentials, dark overlay appears, fades out smoothly (~400ms), then dashboard loads
- [ ] Dashboard navbar is dark (`#0A0A0F` background), "Chat9" logo visible in white on the left
- [ ] Nav links (Dashboard, Documents, Logs, Review, Debug) are visible with white/faded text
- [ ] User email appears on the right side of navbar
- [ ] Logout button is pink (`#E879F9`) ghost style, no background
- [ ] Email verification warning (amber) still shows when not verified
- [ ] No console errors
- [ ] Landing page is completely unchanged — visually identical to before

---

## GIT PUSH

```bash
git add frontend/components/AuthTransition.tsx \
        frontend/components/Navbar.tsx \
        frontend/app/(auth)/login/page.tsx \
        frontend/app/(app)/layout.tsx
git commit -m "feat: auth fade transition + dark brand navbar (FI-UI)"
git push origin feature/ui-brand-transition
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `AuthTransition` uses inline `style` for opacity transition (not Tailwind `animate-`) — more reliable for dynamic opacity
- Do NOT use `framer-motion` for the transition — no new dependencies
- The overlay `pointerEvents: "none"` ensures user can't accidentally interact during fade
- `router.replace` (not `router.push`) so back button doesn't go to login
- The existing `api.clients.getMe()` call is already in Navbar — just add `setUserEmail(c.email)` to that same `.then()` block

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Adds a smooth dark fade transition after login and replaces the light Navbar with a dark brand navbar matching the landing page design.

## Changes
- `components/AuthTransition.tsx` — new component: full-screen `#0A0A0F` overlay that fades out in 400ms, then calls `onComplete`
- `app/(auth)/login/page.tsx` — triggers `AuthTransition` after successful login before redirecting to dashboard
- `components/Navbar.tsx` — replaced light navbar with dark brand navbar (`#0A0A0F` bg, white logo, pink logout, user email)
- `app/(app)/layout.tsx` — explicit `bg-[#F8F9FA]` (no visual change, just makes intent clear)

## Testing
- [ ] Login fade transition works smoothly
- [ ] Dashboard navbar matches landing page brand
- [ ] Logout works
- [ ] Email verification warning still shows
- [ ] Landing page unchanged

## Notes
No new dependencies. AuthTransition uses CSS transition via inline style for reliable opacity animation.
```
