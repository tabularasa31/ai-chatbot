# FI-035: Cleanup Unused UI Components

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/FI-035-cleanup-ui
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/components/ui/` — delete unused components
- `frontend/components/ui/use-mobile.ts` — keep (utility)
- `frontend/components/ui/utils.ts` — keep (utility)

**Do NOT touch:**
- `frontend/components/marketing/` — keep as-is
- Backend, database, migrations
- Any other files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

Landing page build is failing because `components/ui/` folder contains **all 50+ shadcn/ui components** from Figma export, but the landing page uses only a few:

**Actually used:**
- button.tsx
- card.tsx (likely)
- Maybe a few others

**Not used (safe to delete):**
- accordion.tsx
- alert-dialog.tsx
- alert.tsx
- aspect-ratio.tsx
- avatar.tsx
- badge.tsx
- breadcrumb.tsx
- carousel.tsx
- chart.tsx
- checkbox.tsx
- collapsible.tsx
- command.tsx
- context-menu.tsx
- dialog.tsx
- drawer.tsx
- dropdown-menu.tsx
- form.tsx
- hover-card.tsx
- input-otp.tsx
- input.tsx
- label.tsx
- menubar.tsx
- navigation-menu.tsx
- pagination.tsx
- popover.tsx
- progress.tsx
- radio-group.tsx
- resizable.tsx
- scroll-area.tsx
- select.tsx
- separator.tsx
- sheet.tsx
- sidebar.tsx
- skeleton.tsx
- slider.tsx
- sonner.tsx
- switch.tsx
- table.tsx
- tabs.tsx
- textarea.tsx
- toggle-group.tsx
- toggle.tsx
- tooltip.tsx

---

## WHAT TO DO

### 1. Delete Unused Components

Delete all UI component files except:
- `button.tsx` ✅ (used in CTABanner, Hero, etc.)
- `card.tsx` ✅ (possibly used in Features)
- `use-mobile.ts` ✅ (utility)
- `utils.ts` ✅ (utility)

**Delete command (verify before running):**
```bash
rm frontend/components/ui/accordion.tsx
rm frontend/components/ui/alert-dialog.tsx
rm frontend/components/ui/alert.tsx
# ... etc (all except button, card, use-mobile, utils)
```

Or manually delete the 45+ files you don't need.

### 2. Verify Button Component

Check that `frontend/components/ui/button.tsx` exists and has no missing imports.

### 3. Check Marketing Components

Verify that marketing components only import:
```tsx
import { Button } from '@/components/ui/button'
// and not: import { Dialog } from '@/components/ui/dialog' or similar
```

---

## TESTING

Before pushing:
- [ ] `npm run build` passes without errors
- [ ] No more "Cannot find module" errors
- [ ] Landing page still renders correctly
- [ ] Button, Card, and other visual elements work

---

## GIT PUSH

```bash
git add frontend/components/ui/
git commit -m "chore: remove unused UI components to fix build (FI-035)"
git push origin feature/FI-035-cleanup-ui
```

Then open PR, review, and merge.

---

## NOTES

- Figma export came with entire shadcn/ui library
- We only need ~3-4 components for landing page
- Deleting unused ones reduces bundle size and import errors
- If needed in future, can reinstall via shadcn/ui CLI
