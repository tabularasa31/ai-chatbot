# FI-035: Fix ESLint Errors in Landing Page Components

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/FI-035-fix-eslint
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/marketing/DemoBlock.tsx`
- `frontend/components/marketing/figma/ImageWithFallback.tsx`

**Do NOT touch:**
- Other components
- Backend, database, migrations
- Any other files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

Landing page deployment failed due to ESLint errors in 2 components.

### Error 1: DemoBlock.tsx (Lines 71:26 and 101:61)

**Error:** `react/no-unescaped-entities`

```
71:26 Error: ' can be escaped with &apos;, &lsquo;, &#39;, &rsquo;.
```

**Problem:** Single quotes in JSX text that should be escaped.

**Example in file:**
```tsx
// Line 71 has unescaped apostrophe in text like "that's" or "isn't"
// Line 101 has similar issue
```

**Fix:** Replace straight single quotes with HTML entities:
- `'` → `&apos;` or `&#39;` (in JSX text)
- Or use double quotes around the word: `that&rsquo;s` if needed
- Or restructure text to avoid apostrophes in inline text

### Error 2: ImageWithFallback.tsx (Lines 28, 42)

**Warning:** Using `<img>` could result in slower LCP and higher bandwidth. Consider using `<Image />` from `next/image`.

**Problem:** Component uses raw `<img>` tags instead of Next.js `<Image>` component.

**Current implementation:**
```tsx
<img src={src} alt={alt} className={className} style={style} {...rest} />
```

**Expected implementation:**
```tsx
import Image from 'next/image'

// Use Image component for external URLs (with width/height)
// Keep img for data: URLs or local paths
```

---

## WHAT TO DO

### DemoBlock.tsx

1. Find all instances of unescaped single quotes in text content (lines ~71 and ~101)
2. Replace with one of these options:
   - `&apos;` (safest)
   - `&#39;` (numeric entity)
   - Restructure sentence to avoid apostrophe
   - Use template literal if possible

Example fix:
```tsx
// Before:
<p className="text-[#FAF5FF]">That's how it works.</p>

// After (option 1 - entity):
<p className="text-[#FAF5FF]">That&apos;s how it works.</p>

// After (option 2 - restructure):
<p className="text-[#FAF5FF]">This is how it works.</p>
```

### ImageWithFallback.tsx

Keep the fix I provided (use Image component for external URLs, img for data: URLs).

If ESLint still complains:
- Ensure you import Image from next/image
- For dynamic widths, use layout="responsive" or width="100%" height="auto"
- Add `@ts-ignore` comment if type errors occur (not ideal but acceptable for now)

---

## TESTING

Before pushing:
- [ ] `npm run build` passes without errors
- [ ] ESLint shows 0 errors (warnings are OK for now)
- [ ] DemoBlock.tsx lines 71 and 101 no longer have errors
- [ ] ImageWithFallback.tsx has no errors

---

## GIT PUSH

```bash
git add frontend/components/marketing/DemoBlock.tsx frontend/components/marketing/figma/ImageWithFallback.tsx
git commit -m "fix: resolve ESLint errors in landing page components (FI-035)"
git push origin feature/FI-035-fix-eslint
```

Then open PR, review, and merge.

---

## NOTES

- Deployment is blocked by these ESLint errors
- Once fixed, Vercel will auto-rebuild and deploy
- Check Vercel dashboard to confirm deployment success
