# FI-UI: Add "Sign in" Button to Landing Page Navigation

⚠️ **CRITICAL: Follow SETUP exactly. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/add-signin-navigation
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/marketing/Navigation.tsx` — only file to change

**Do NOT touch:**
- Any other files
- Existing styles (keep dark theme, colors, fonts)

---

## CONTEXT

The landing page navigation currently has:
```
[Chat9 logo]    [Docs]  [GitHub]         [Try for free →]
```

Missing: a "Sign in" link for existing users. They have no way to access the dashboard from the landing page.

Target layout:
```
[Chat9 logo]    [Docs]  [GitHub]    [Sign in]  [Try for free →]
```

**Current code (Navigation.tsx):**
```tsx
{/* CTA Button - Desktop */}
<Link
  href="/signup"
  className="hidden md:block bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg ..."
>
  Try for free
</Link>
```

---

## WHAT TO DO

### Desktop navigation

Add "Sign in" link **before** the "Try for free" button:

**Before:**
```tsx
{/* CTA Button - Desktop */}
<Link
  href="/signup"
  className="hidden md:block bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all"
>
  Try for free
</Link>
```

**After:**
```tsx
{/* Sign in + CTA - Desktop */}
<div className="hidden md:flex items-center gap-3">
  <Link
    href="/login"
    className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors px-4 py-2"
  >
    Sign in
  </Link>
  <Link
    href="/signup"
    className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all"
  >
    Try for free
  </Link>
</div>
```

### Mobile menu

Add "Sign in" link inside the mobile menu, before the "Try for free" button:

**Before:**
```tsx
<Link
  href="/signup"
  className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg ..."
>
  Try for free
</Link>
```

**After:**
```tsx
<Link
  href="/login"
  className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors text-left"
>
  Sign in
</Link>
<Link
  href="/signup"
  className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] transition-colors inline-block text-center"
>
  Try for free
</Link>
```

---

## TESTING

- [ ] Desktop: "Sign in" link visible between GitHub and "Try for free"
- [ ] Desktop: clicking "Sign in" → navigates to /login
- [ ] Mobile: hamburger menu shows "Sign in" above "Try for free"
- [ ] Styles consistent with existing nav (muted text, no background)
- [ ] No layout breaks on mobile or desktop
- [ ] No TypeScript/ESLint errors

---

## GIT PUSH

```bash
git add frontend/components/marketing/Navigation.tsx
git commit -m "feat: add Sign in button to landing page navigation"
git push origin feature/add-signin-navigation
```

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
[1-2 sentences: what was changed and why]

## Changes
- [file.py] — [what changed]

## Testing
- [ ] Tests pass (pytest)
- [ ] Manual test: [specific scenario]

## Notes
[Any important context, limitations, or follow-up work]
```
