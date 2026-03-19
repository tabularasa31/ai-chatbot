# FI-035: Landing Page (getchat9.live)

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/FI-035-landing-page
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `frontend/app/page.tsx` — replace redirect with landing page component
- `frontend/app/(marketing)/` — create new segment for marketing pages
- `frontend/app/(marketing)/layout.tsx` — marketing layout (nav + footer)
- `frontend/app/(marketing)/page.tsx` — landing page assembly
- `frontend/components/marketing/*` — new marketing-specific components
- `frontend/public/images/` — hero image/animation assets
- `frontend/public/logo.svg` — brand logo (if not exists)
- `frontend/tailwind.config.js` — add color tokens for Chat9 (optional)

**Do NOT touch:**
- `frontend/app/(app)/` — authenticated routes (dashboard, admin, etc.)
- `frontend/app/(auth)/` — auth pages
- Backend files, database, migrations
- Existing components in `frontend/components/` (unless reusing)

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

### Chat9 Project Overview

- **Product:** AI support bot for SaaS companies
- **Positioning:** "Your support mate, always on" — friendly, not corporate
- **Domain:** getchat9.live (live & ready)
- **Deployment:** Vercel (frontend) + Railway (backend)

### Current State

- ✅ Demo widget (working, embedded in app)
- ✅ Admin dashboard (client login, settings)
- ✅ Backend API (RAG pipeline, chat, embeddings)
- ❌ Public landing page (missing)

### What You're Building

**Sections (in order):**

1. **Navigation** (sticky, minimal)
   - Logo (left)
   - Links: [Home] (Docs) (GitHub)
   - CTA: [Try for free] (right)

2. **Hero**
   - Headline: "Meet your new support mate."
   - Subheadline: "Works 24/7. Sends you a daily report. Gets better every week."
   - CTA: "Try for free" (primary) + "See demo" (secondary)
   - Visual: Hero image (right side, 1:1 or 16:9)

3. **Features** (4 features in grid)
   - Icon + headline + description for each:
     * "Load docs in 2 minutes" — simple upload
     * "Works 24/7" — always available
     * "Daily reports" — email summaries
     * "Understands context" — RAG + multi-language

4. **Demo Widget**
   - Heading: "See Chat9 in action"
   - Embed: Live Chat9 widget (real conversations)
   - Demo API key: Use `process.env.NEXT_PUBLIC_DEMO_API_KEY`

5. **Metrics / Social Proof**
   - Hardcoded values (for now):
     * "47 sessions this week"
     * "143 messages"
     * "12,450 tokens used"
   - Future: connect to real API endpoint

6. **Final CTA**
   - Headline: "Ready to meet your support mate?"
   - Button: "Try for free"

7. **Footer**
   - Logo/branding (left)
   - Links: Docs, GitHub (center)
   - Copyright: © 2026 Chat9 (right)

### Design Assets

- **Logo:** `/public/logo.svg` (Chat9 text logo, SVG)
- **Hero image:** `/public/images/hero-widget.png` (generated via Midjourney, 1920x1080 PNG)
- **Color palette:** (TBD by designer, but suggest blue + orange/teal accents)
- **Typography:** Modern sans-serif (Inter, Poppins, Clash Grotesk)
- **Spacing:** 80px sections (desktop), 40px (mobile)

### Key Links (for Footer & CTAs)

- **Docs:** `/demo-docs` (on getchat9.live, or link to GitHub `ai-chatbot/demo-docs/`)
- **GitHub:** https://github.com/tabularasa31/ai-chatbot
- **Try for free:** `/signup` (redirect to auth signup)
- **See demo:** Scroll to demo widget (anchor link `#demo`)

### Environment Variables

```
NEXT_PUBLIC_DEMO_API_KEY=<your-demo-client-api-key>
```

Create this in `.env.local` before building. The key is for the embedded widget to show real responses.

---

## IMPLEMENTATION GUIDE

### 1. Create Marketing Layout

**File:** `frontend/app/(marketing)/layout.tsx`

```tsx
export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col min-h-screen">
      <MarketingNavbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  );
}
```

### 2. Create Marketing Navbar

**File:** `frontend/components/marketing/MarketingNavbar.tsx`

- Logo (left, clickable → home)
- Nav links: Home, Docs, GitHub
- CTA button: "Try for free" (→ /signup)
- Sticky on scroll
- Responsive (hamburger on mobile)

### 3. Create Landing Page

**File:** `frontend/app/(marketing)/page.tsx`

Assembly of sections:
- HeroSection
- FeaturesGrid
- WidgetDemo (with id="demo" for anchor)
- MetricsSection
- CTASection

### 4. Create Page Components

**Files in `frontend/components/marketing/`:**

- `HeroSection.tsx` — headline, subheadline, CTAs, hero image
- `FeaturesGrid.tsx` — 4 features with icons
- `WidgetDemo.tsx` — embedded Chat9 widget (with demo API key)
- `MetricsSection.tsx` — hardcoded session/message/token counts
- `CTASection.tsx` — final "Ready to meet..." CTA
- `Footer.tsx` — links, copyright
- `Button.tsx` — reusable button component (primary / secondary)
- `SectionHeading.tsx` — reusable section title (h2 + optional description)
- `Container.tsx` — width-constrained wrapper (max-w-6xl)

### 5. Update Root Page

**File:** `frontend/app/page.tsx`

Replace the redirect with:
```tsx
import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth"; // or your auth util

export default async function RootPage() {
  const session = await getSession();
  if (session) {
    redirect("/dashboard");
  }
  return <MarketingPage />;
}
```

Or simpler: just redirect `/` to `/(marketing)` in middleware.

### 6. Add Assets

- Place hero image: `frontend/public/images/hero-widget.png`
- Ensure logo exists: `frontend/public/logo.svg`
- Add favicon if missing: `frontend/public/favicon.ico`

### 7. Responsive Design

- Desktop (1200px+): side-by-side hero, 4-col features grid
- Tablet (768px+): center hero, 2-col features grid
- Mobile (<768px): stacked, 1-col features grid, full-width sections

### 8. Accessibility

- Use semantic HTML (`<section>`, `<header>`, `<footer>`)
- Alt text for images
- Sufficient color contrast (WCAG AA)
- Focus states for buttons/links

---

## TESTING CHECKLIST

Before pushing:

- [ ] Page loads without errors
- [ ] Hero displays correctly (image + text side-by-side on desktop)
- [ ] Features grid responsive (4 → 2 → 1 columns)
- [ ] Demo widget loads and is interactive (requires NEXT_PUBLIC_DEMO_API_KEY)
- [ ] All CTA buttons navigate correctly:
  - "Try for free" → /signup
  - "See demo" → scroll to #demo widget
  - Footer "Docs" → correct URL
  - Footer "GitHub" → correct URL
- [ ] Navigation sticky on scroll
- [ ] Mobile menu works (hamburger)
- [ ] Footer appears at bottom
- [ ] No layout shifts (CLS issues)
- [ ] Images optimized (use Next.js `<Image>` component)
- [ ] Lighthouse score >80 (performance)

---

## GIT PUSH

```bash
git add .
git commit -m "feat: build landing page for getchat9.live (FI-035)"
git push origin feature/FI-035-landing-page
```

Then create PR on GitHub.

---

## NOTES

- **Demo widget:** Uses embed snippet from `backend/widget/static/embed.js`. Ensure it's served correctly.
- **Hero image:** If Midjourney image isn't ready, use placeholder from Unsplash/unDraw temporarily.
- **Colors:** Use TailwindCSS utilities (e.g., `bg-blue-600`, `text-gray-900`). If custom palette is defined, add to `tailwind.config.js`.
- **Fonts:** Next.js should already have a system font stack. If you need custom fonts (Google Fonts), add to `app/layout.tsx`.

---

## EXPECTED OUTCOME

✅ Public landing page at `getchat9.live/` showing:
- Friendly, modern design
- Live Chat9 widget demo
- Clear CTAs to sign up
- Mobile-responsive
- Fast load time
