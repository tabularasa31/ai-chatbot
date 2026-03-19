# FI-UI: Auth Pages Dark Theme — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/auth-pages-dark-theme
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
- `frontend/app/(auth)/signup/page.tsx`
- `frontend/app/(auth)/forgot-password/page.tsx`
- `frontend/app/(auth)/reset-password/page.tsx`
- `frontend/app/(auth)/verify/page.tsx`

**Do NOT touch:**
- API routes or backend files
- Middleware or layout files
- Other modules or components
- migrations

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** Auth pages (login, signup, forgot password, reset password, verify) currently use a light theme with generic slate/blue colors. This doesn't match the landing page design system, creating visual inconsistency.

**Landing page design system** (from `frontend/components/marketing/Navigation.tsx`):
- **Background:** `#0A0A0F` (dark navy/black)
- **Text:** `#FAF5FF` (off-white/lavender)
- **Primary accent (magenta):** `#E879F9`
- **Secondary accent (cyan):** `#67E8F9`
- **Borders/dividers:** `#1E1E2E` and `#2E2E3E` (dark gray)

**Why this matters:** Users expect consistent visual experience when moving from marketing site → auth flow → dashboard. Currently, auth pages feel disconnected and unprofessional.

---

## WHAT TO DO

### 1. Update Login Page (`frontend/app/(auth)/login/page.tsx`)

Replace the entire JSX return statement with dark theme styling:

**Color mappings:**
- Background: `bg-[#0A0A0F]`
- Card background: `bg-[#1E1E2E]`
- Card border: `border border-[#2E2E3E]`
- Primary text: `text-[#FAF5FF]`
- Secondary text: `text-[#FAF5FF]/80` or `text-[#FAF5FF]/60`
- Input background: `bg-[#0A0A0F]`
- Input border: `border-[#2E2E3E]`
- Input text: `text-[#FAF5FF]`
- Input placeholder: `placeholder-[#FAF5FF]/40`
- Focus ring: `focus:ring-[#E879F9]` (magenta accent)
- Button background: `bg-[#E879F9]`
- Button text: `text-[#0A0A0F]`
- Button hover: `hover:bg-[#f099fb]`
- Links: `text-[#E879F9]` with `hover:underline`
- Error text: `text-[#FF6B6B]` with `bg-[#FF6B6B]/10 border border-[#FF6B6B]/20`

**Key changes:**
- Replace `bg-slate-50` → `bg-[#0A0A0F]`
- Replace `bg-white` → `bg-[#1E1E2E]`
- Replace `text-slate-*` → `text-[#FAF5FF]` or variants
- Replace `border-slate-300` → `border-[#2E2E3E]`
- Replace `focus:ring-blue-600` → `focus:ring-[#E879F9]`
- Replace `bg-blue-600` button → `bg-[#E879F9] text-[#0A0A0F]`
- Add `hover:scale-105 transition-all` to button for polish
- Update focus ring offset color: `focus:ring-offset-[#0A0A0F]`

### 2. Update Signup Page (`frontend/app/(auth)/signup/page.tsx`)

Apply **identical styling** as login page, only changing:
- Heading text from "Sign in" → "Create account"
- Button text from "Sign in" / "Signing in..." → "Sign up" / "Creating account..."
- Link text from "Sign in" → "Sign up"
- Copy from "Don't have an account?" → "Already have an account?"

All colors and styling must match login exactly.

### 3. Update Forgot Password Page (`frontend/app/(auth)/forgot-password/page.tsx`)

Apply **identical dark theme styling** as login/signup, with:
- Heading: "Reset password"
- Input: single email field
- Button: "Send reset link"
- Copy: "Back to" + link to login

### 4. Update Reset Password Page (`frontend/app/(auth)/reset-password/page.tsx`)

Apply **identical dark theme styling**, with:
- Heading: "Set new password"
- Inputs: password + confirm password fields
- Button: "Reset password"

### 5. Update Verify Page (`frontend/app/(auth)/verify/page.tsx`)

Apply **identical dark theme styling**, with:
- Heading: "Verify your email"
- Input: verification code field
- Button: "Verify"

---

## TESTING

Before pushing:
- [ ] Login page renders with dark theme (no light colors visible)
- [ ] Signup page matches login styling exactly
- [ ] Forgot password, reset password, verify pages all use same dark theme
- [ ] Focus states on inputs show magenta accent (`#E879F9`)
- [ ] Buttons are magenta with hover effect (slight brightness increase + scale)
- [ ] Error messages display with red background and text
- [ ] Links use magenta color with underline on hover
- [ ] Form backgrounds are dark (`#0A0A0F`), cards are darker shade (`#1E1E2E`)
- [ ] Text contrast is readable (light text on dark background)
- [ ] Navigation from landing page to login/signup maintains visual consistency
- [ ] No hardcoded blue colors remain in auth pages

---

## GIT PUSH

```bash
git add frontend/app/\(auth\)/*.tsx
git commit -m "feat: dark theme for auth pages (FI-UI)"
git push origin feature/auth-pages-dark-theme
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- **Color consistency is critical:** Use exact hex values from marketing components (`#0A0A0F`, `#FAF5FF`, `#E879F9`, etc.)
- **Opacity variants:** `text-[#FAF5FF]/80` means 80% opacity. Use `/60`, `/40` for secondary text.
- **Hover effects:** Buttons should scale slightly on hover (`hover:scale-105`) for modern feel, matching navigation style.
- **Error styling:** Red (`#FF6B6B`) is custom; ensure proper contrast against dark background.
- **Ring offset:** When showing focus ring, offset color must match page background (`focus:ring-offset-[#0A0A0F]`).

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Unified auth pages (login, signup, forgot password, reset password, verify) with dark theme to match landing page design system. All pages now use consistent color palette and styling for professional, cohesive user experience.

## Changes
- `frontend/app/(auth)/login/page.tsx` — Dark theme styling, magenta accents, dark card backgrounds
- `frontend/app/(auth)/signup/page.tsx` — Dark theme styling to match login
- `frontend/app/(auth)/forgot-password/page.tsx` — Dark theme styling, consistent with other auth pages
- `frontend/app/(auth)/reset-password/page.tsx` — Dark theme styling, password fields
- `frontend/app/(auth)/verify/page.tsx` — Dark theme styling, email verification UI

## Testing
- [x] All auth pages render with dark theme (`#0A0A0F` background, `#1E1E2E` cards)
- [x] Focus states show magenta accent (`#E879F9`)
- [x] Buttons are magenta with hover effects (brightness + scale)
- [x] Text contrast is readable and accessible
- [x] Error messages display with red styling
- [x] Navigation between auth pages maintains visual consistency
- [x] Landing page → auth pages transition is seamless

## Notes
- Colors sourced from `frontend/components/marketing/Navigation.tsx` design system
- All hex values are exact matches to landing page components
- Magenta (`#E879F9`) is primary brand accent across all pages
- Dark theme improves visual hierarchy and brand recognition
```
