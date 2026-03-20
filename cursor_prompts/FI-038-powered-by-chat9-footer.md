# FI-038: "Powered by Chat9" Widget Footer — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b feature/fi-038-powered-by-chat9
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/ChatWidget.tsx` — add footer below input area

**Do NOT touch:**
- Backend files
- migrations
- Other frontend components
- `frontend/app/widget/page.tsx`

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** The widget has no branding. Every embedded widget is free advertising — we're missing passive viral distribution (like "Sent from iPhone").

**Current state:** `ChatWidget.tsx` ends with input + Send button, no footer.

**Goal:** Small unobtrusive "Powered by Chat9" line at the bottom of every widget. In the future, Premium clients can hide it.

---

## WHAT TO DO

In `frontend/components/ChatWidget.tsx`, add a footer div after the input row (inside the outermost return div):

```tsx
{/* Powered by Chat9 footer */}
<div
  style={{
    textAlign: "center",
    paddingTop: "8px",
    fontSize: "11px",
    color: "#9ca3af",
  }}
>
  Powered by{" "}
  <a
    href="https://getchat9.live"
    target="_blank"
    rel="noopener noreferrer"
    style={{
      color: "#6b7280",
      textDecoration: "none",
      fontWeight: 500,
    }}
  >
    Chat9
  </a>
</div>
```

Place it as the last child inside the outermost `<div>` of the return statement, after the input row div.

---

## TESTING

Before pushing:
- [ ] Widget loads without errors
- [ ] "Powered by Chat9" is visible at the bottom
- [ ] Link opens `https://getchat9.live` in a new tab
- [ ] Footer is small and unobtrusive — does not interfere with input area
- [ ] No TypeScript errors (`npm run build`)

---

## GIT PUSH

```bash
git add frontend/components/ChatWidget.tsx
git commit -m "feat: add 'Powered by Chat9' footer to widget (FI-038)"
git push origin feature/fi-038-powered-by-chat9
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `rel="noopener noreferrer"` is required for security with `target="_blank"`
- Future Premium feature: hide this footer via client settings flag

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Added "Powered by Chat9" footer to the embedded widget for passive brand awareness.

## Changes
- `frontend/components/ChatWidget.tsx` — added footer with link to getchat9.live

## Testing
- [ ] Footer visible in widget
- [ ] Link opens getchat9.live in new tab
- [ ] Build passes (no TS errors)
- [ ] Styling unobtrusive

## Notes
Future: hide footer for Premium clients via settings flag.
```
