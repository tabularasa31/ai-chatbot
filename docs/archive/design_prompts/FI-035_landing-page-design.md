# FI-035: Landing Page Design Brief (for Figma/Designer)

## Overview

Modern B2B SaaS landing page for Chat9 — positioning as "Your support mate, always on."

**Domain:** getchat9.live
**Stack:** Next.js + TailwindCSS
**Target:** SaaS companies looking for AI support automation

---

## Design Principles

- **Friendly, not corporate** — "mate" not "enterprise solution"
- **Modern minimalist** — inspired by Linear, Notion, Clerk
- **Dark mode friendly** — consider both light & dark themes
- **Mobile-first** — responsive on all devices
- **Interactive** — subtle animations (fade, slide, hover effects)

---

## Color Palette (Proposed)

Define or provide:
- **Primary:** [TBD — suggest: modern blue like Linear/Notion]
- **Secondary:** [TBD — accent for CTAs, perhaps orange or teal]
- **Neutral:** Grays for text/backgrounds (light & dark variants)
- **Accent:** For highlights, hover states, success messages

Example (adjust as needed):
- Primary: #2563EB (blue)
- Secondary: #FF6B35 (orange)
- Background light: #FFFFFF / dark: #0F172A
- Text light: #1F2937 / dark: #F3F4F6

---

## Logo

**Status:** Needs finalization
- Simple text logo "Chat9" in modern sans-serif (e.g., Inter, Poppins, Clash Grotesk)
- Option: Icon + wordmark (e.g., speech bubble or gear icon)
- Size: SVG format, scalable (32px icon, 24px text)
- File: `frontend/public/logo.svg`

---

## Page Structure & Sections

### 1. Navigation (Header)
- Logo (left)
- Nav links: [Home] (Docs) (GitHub) [Try for free]
- Sticky or disappear on scroll (designer choice)
- Mobile: hamburger menu

### 2. Hero Section
```
┌─────────────────────────────────────────────┐
│                                             │
│  Meet your new support mate.                │  <- Left: headline + subheadline
│  Works 24/7. Sends you a daily report.      │
│  Gets better every week.                    │
│                                             │
│  [Try for free] [See demo]                  │  <- CTAs
│                                             │
│                 [HERO IMAGE]                │  <- Right: animated GIF/image
│                                             │
└─────────────────────────────────────────────┘
```
- **Headline:** "Meet your new support mate."
- **Subheadline:** "Works 24/7. Sends you a daily report. Gets better every week."
- **CTA Primary:** "Try for free" (button, primary color)
- **CTA Secondary:** "See demo" (text link)
- **Image:** Hero animation (1:1 or 16:9, right side)

### 3. Features Section
Grid of 4 features (2x2 or 4x1 on mobile):
```
[Icon] Load docs in 2 minutes    [Icon] Works 24/7
       Simple upload                     Always available

[Icon] Daily reports             [Icon] Understands context
       Email summaries                   RAG + multi-language
```
- **Card layout:** Icon (60px) + headline (16px bold) + description (14px gray)
- **Spacing:** Consistent padding, grid gap ~40px
- **Icons:** Lucide/Phosphor or simple SVG (upload, clock, mail, brain)

### 4. Demo Section
```
┌─────────────────────────────────────────────┐
│  See Chat9 in action                        │
│  Ask it anything about our docs             │
│                                             │
│           [LIVE WIDGET EMBED]               │
│           (real Chat9 widget here)          │
│                                             │
└─────────────────────────────────────────────┘
```
- **Title:** "See Chat9 in action"
- **Subtitle:** "Ask it anything about our docs"
- **Widget container:** Iframe or embed snippet for live widget
- **Height:** ~600px (scrollable chat)
- **Border:** Subtle shadow, rounded corners

### 5. Social Proof Section (Future-ready)
```
📊 47 sessions this week  |  143 messages  |  12,450 tokens used
```
- **Metrics:** Three simple KPIs (sessions, messages, tokens)
- **Layout:** Flex row, centered, large numbers
- **Placeholder:** Hardcode values for launch, connect to real API later

### 6. CTA Section (Close)
```
Ready to meet your support mate?
[Try for free]
```
- **Headline:** "Ready to meet your support mate?"
- **Button:** Primary color, large, centered
- **Spacing:** Generous vertical padding (80-120px)

### 7. Footer
```
Chat9  |  Docs  GitHub  |  © 2026 Chat9
```
- **Logo/Branding** (left)
- **Links:** Docs, GitHub (center)
- **Copyright:** Right
- **Dark background** (slightly different from body)
- **Mobile:** Stack vertically

---

## Typography

- **Headlines (H1, H2, H3):** Bold sans-serif, 2-3 sizes
  - H1: 48px (desktop), 32px (mobile)
  - H2: 32px (desktop), 24px (mobile)
  - H3: 24px (desktop), 20px (mobile)
- **Body:** 16px, line-height 1.6
- **Small text:** 14px (descriptions, footer)
- **Font family:** Modern sans-serif (Inter, Poppins, or similar)

---

## Spacing & Layout

- **Container max-width:** 1200px (or adjust as needed)
- **Section padding:** 80px vertical, 40px horizontal (desktop)
- **Section padding:** 40px vertical, 20px horizontal (mobile)
- **Grid gap:** 40px
- **Button padding:** 12px 24px (medium), 16px 32px (large)

---

## Buttons & Interactive Elements

**Primary Button:**
- Background: primary color
- Text: white
- Padding: 16px 32px
- Border radius: 8px
- Hover: slightly darker or scale effect
- Font weight: 600

**Secondary Button / Link:**
- Background: transparent
- Text: primary color
- Border: 1px solid primary (optional)
- Hover: background light shade of primary

**Hover effects:**
- Subtle scale (1.05x)
- Shadow increase
- Color shift (darker/lighter)

---

## Responsive Breakpoints

- **Desktop:** 1200px+
- **Tablet:** 768px - 1199px
- **Mobile:** < 768px

Key changes:
- Mobile: sections stack vertically, full width
- Hero: image below headline (not side-by-side)
- Features: 1 column on mobile, 2 on tablet, 4 on desktop
- Navigation: hamburger menu on mobile

---

## Animation & Micro-interactions

- **Section entrance:** Fade-in as user scrolls
- **Button hover:** Scale + shadow
- **Links:** Color transition (300ms)
- **Hero image:** Subtle pulse or float effect
- **Keep it light:** No more than 2-3 animation effects total

---

## Deliverables

1. **Figma file** with all sections designed
2. **CSS variables** for colors (for TailwindCSS)
3. **Component specs** (button sizes, spacing, etc.)
4. **Mobile screenshots** to confirm responsive design
5. **Logo file** (SVG)

---

## Reference Designs

Study these for inspiration:
- **Linear.app** — minimalist, friendly, modern
- **Notion.so** — clean hierarchy, great typography
- **Clerk.com** — developer-focused, clear CTAs
- **Retool.com** — professional but approachable

---

## Next Steps

1. Create Figma mockup based on this brief
2. Get feedback on colors, layout, typography
3. Export components (buttons, cards, etc.) as React-ready
4. Hand off to Cursor for implementation
