# SECURITY: Protect /review Endpoint with Middleware

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/security-protect-review
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/middleware.ts` — add `/review` to PROTECTED_PATHS

**Do NOT touch:**
- `/review` page itself (no changes needed)
- Backend routes
- Database, migrations

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Issue:** `/review` endpoint is accessible without authentication.

**What is /review?**
- Internal page for authenticated clients only
- Shows "bad answers" (questions marked with 👎)
- Allows clients to add "ideal answers" for training
- Shows debug info (retrieval chunks, embeddings scores)
- **This is sensitive client data — must be protected!**

**Current state:**
- Page file is in `frontend/app/(app)/review/` (protected route)
- But middleware doesn't explicitly protect it → could be vulnerability

**Solution:**
Add `/review` to `PROTECTED_PATHS` in middleware to ensure it requires authentication.

---

## WHAT TO DO

### Find middleware.ts

Locate `frontend/middleware.ts` (or `app/middleware.ts`).

### Find PROTECTED_PATHS

Look for:
```typescript
const PROTECTED_PATHS = ["/dashboard", "/documents", "/logs", "/debug", "/admin"];
```

### Add /review

Update to:
```typescript
const PROTECTED_PATHS = ["/dashboard", "/documents", "/logs", "/review", "/debug", "/admin"];
```

### Verify matcher (if exists)

If there's a `matcher` pattern, ensure it includes `/review`:

**Before:**
```typescript
export const config = {
  matcher: ["/dashboard/:path*", "/documents/:path*", "/logs/:path*", "/debug/:path*"],
};
```

**After:**
```typescript
export const config = {
  matcher: ["/dashboard/:path*", "/documents/:path*", "/logs/:path*", "/review/:path*", "/debug/:path*"],
};
```

---

## TESTING

Before pushing:
- [ ] Without auth token: `/review` redirects to `/login`
- [ ] With valid token: `/review` loads and shows bad answers
- [ ] Navigation to `/review` from `/dashboard` works correctly
- [ ] No console errors about route protection

---

## GIT PUSH

```bash
git add frontend/middleware.ts
git commit -m "security: protect /review endpoint with middleware"
git push origin feature/security-protect-review
```

Then create PR, review, and merge.

---

## NOTES

- This is a **security fix** — should be prioritized
- `/review` contains sensitive client data
- Middleware enforcement ensures no accidental access
- No functional changes, just adding to protection list
