# FI-035: Replace motion/react with framer-motion

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/FI-035-fix-motion
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/marketing/*.tsx` — all files importing `motion/react`
- `frontend/components/hooks/useScrollAnimation.ts` (if it uses motion/react)

**Do NOT touch:**
- Other components
- Backend, database, migrations
- Any other files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

Landing page uses `motion/react` (from Figma Vite prototype), but Chat9 project uses `framer-motion`.

**Problem:**
- `motion/react` not installed, build fails
- Chat9 already has `framer-motion` in dependencies

**Solution:**
Replace all `motion/react` imports with `framer-motion`.

---

## WHAT TO DO

### Find all files using motion/react

```bash
grep -r "from 'motion/react'" frontend/components/marketing/
grep -r 'from "motion/react"' frontend/components/marketing/
```

These files need updates:
- CTABanner.tsx
- DemoBlock.tsx
- Features.tsx
- Hero.tsx
- Stats.tsx
- (and possibly others)

### Replace imports

**Before:**
```tsx
import { motion } from 'motion/react';
```

**After:**
```tsx
import { motion } from 'framer-motion';
```

### Check usage (should be identical)

Both `motion/react` and `framer-motion` export the same `motion` object, so:
- `<motion.div>`
- `<motion.button>`
- `initial`, `animate`, `transition` props
- All work exactly the same way

No logic changes needed, just replace imports.

### Update useScrollAnimation hook

If `frontend/components/hooks/useScrollAnimation.ts` exists and uses `motion/react`, update it too:

**Before:**
```tsx
import { useInView } from 'motion/react';
```

**After:**
```tsx
import { useInView } from 'framer-motion';
```

---

## TESTING

Before pushing:
- [ ] `npm run build` passes without errors
- [ ] No errors about missing `motion/react` module
- [ ] All animation components render correctly
- [ ] Hover effects and transitions still work (visual check)

---

## GIT PUSH

```bash
git add frontend/components/marketing/ frontend/components/hooks/
git commit -m "fix: replace motion/react with framer-motion (FI-035)"
git push origin feature/FI-035-fix-motion
```

Then open PR, review, and merge.

---

## NOTES

- `framer-motion` is already installed in Chat9
- Both libraries have identical APIs for what we're using
- No behavior changes, just import path updates
