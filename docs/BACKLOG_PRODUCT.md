# Product Features Backlog

Product features for clients and platform operators.
Last updated: 2026-03-21

---

## 🟢 ✅ COMPLETED

### [FI-033] Switch to gpt-4o-mini ✅ DONE
- Model updated: gpt-3.5-turbo → gpt-4o-mini
- System prompt optimized, tests updated (135 passing)
- Status: Production ✅

### [FI-035] Landing Page ✅ DONE
- Live: getchat9.live
- Dark modern, fully responsive, Figma → React
- Sections: Hero, Features (4), Demo widget, Stats, CTA, Footer
- Status: Production ✅

### [SECURITY] /review endpoint protection ✅ DONE
### [SECURITY] CORS Configuration ✅ DONE
### [FI-015/016/017] Email verification via Brevo ✅ DONE
### [FI-018] Token tracking ✅ DONE
### [FI-014] Admin metrics MVP ✅ DONE
### [FI-010] 👍/👎 feedback + Review bad answers ✅ DONE

---

## 🔴 P1 — Do now (в порядке запуска)

> Priority order revised per product strategy (2026-03-21).
> Focus: build Switching Cost moats first — they work fastest.

### [FI-038] "Powered by Chat9" widget footer ⚡ 30 MIN
**Why now:** Passive viral marketing. Each embedded widget = free ad on client's site.

- Add small "Powered by Chat9 →" link at bottom of chat window
- Style: small gray text, non-intrusive
- Future: Premium tier removes branding ("Remove branding" as upsell)
- File: `backend/widget/static/embed.js`

**Effort:** 30 minutes. Do this first.

---

### [FI-032] Document Health Check → Gap Analyzer v1 🌟 CRITICAL DIFFERENTIATOR
**Why critical:** Core of the product strategy. Gap Analyzer is Chat9's #1 differentiator vs ALL Tier 3 competitors. Must be on all plans. Cursor prompt ready.

**What it does:**
- After document upload → GPT-4o-mini analyzes structure
- Returns warnings: missing sections, poor structure, outdated content, no examples
- Score 0–100 shown in dashboard with colored badge
- "Re-check" button per document

**Phase 1 (now):** Structural analysis per document — FI-032 prompt ready.

**Phase 2 (later):** Cross-tenant benchmarks — "products like yours get questions about X, but you have no page on X." Requires data accumulation from multiple tenants.

**Cursor prompt:** `cursor_prompts/FI-032-document-health-check.md`
**Effort:** 3 days.

---

### [FI-KYC] Know Your Customer — User Identification
**Why:** Switching Cost moat. KYC means clients' support workflows depend on Chat9.
Per strategy: "A bot that cannot identify its users is not production-ready at any price point."

**What it does:**
- Widget can identify users: email, user_id, company name passed via JS embed
- Logged on every conversation: who asked what
- Client dashboard shows "user X asked 5 questions this week"
- Optional: require email before chat starts (toggle in settings)

**Implementation:**
- `data-user-email`, `data-user-id` attributes on embed script
- Pass through widget → backend → store on Chat/Message
- Dashboard: user-level view in /logs

**Effort:** 2 days.

---

### [FI-ESC] L2 Escalation Tickets
**Why:** Switching Cost moat. When bot can't answer → creates a ticket instead of "I don't know."

**What it does:**
- Bot detects low-confidence answer (score < threshold)
- Offers: "Want me to create a support ticket for this?"
- User confirms → ticket created (internal log or integrated with Zendesk/email)
- Client dashboard: ticket inbox with unanswered questions

**v1 (internal):** ticket = row in DB, visible in dashboard + email notification to client.
**v2:** Zendesk/Intercom integration.

**Effort:** 3 days (v1).

---

### [FI-DISC] Disclosure Controls
**Why:** Switching Cost moat + enterprise requirement.
Per strategy: "A bot that cannot control what it reveals is not production-ready."

**What it does:**
- Client defines topics the bot must NOT discuss (pricing, competitors, legal)
- Bot redirects these to human agent / support email
- Example: "I can't discuss pricing — please contact sales@company.com"

**Implementation:**
- `disclosure_rules` JSON on Client model
- In `build_rag_prompt()` — inject "Do NOT discuss: X, Y, Z. Redirect to: [contact]"
- Dashboard UI: simple list of restricted topics + redirect contact

**Effort:** 2 days.

---

### [FI-008] Hybrid Search: BM25 + RRF
Cursor prompt ready: `cursor_prompts/FI-019ext-bm25-hybrid-hnsw.md`
See BACKLOG_RAG_QUALITY.md

---

### [FI-043] PII Redaction (Regex)
Cursor prompt ready: `cursor_prompts/FI-043-pii-redaction-regex.md`
See BACKLOG_SECURITY.md

---

### [FI-009] Improved Chunking
Cursor prompt ready: `cursor_prompts/FI-009-improved-chunking.md`
See BACKLOG_RAG_QUALITY.md

---

## 🟠 P2 — Next sprint

### [FI-ONBOARD] Conversational Onboarding (4-question flow)
**Why:** Reduces time-to-first-value. Per strategy: "4 questions, bot is live. No loading screens."

**Flow:**
1. "What's your product called and what does it do?" (1 sentence)
2. "Paste your documentation URL" → parsing starts in background
3. "What should the bot say when it can't answer?" → disclosure default
4. "What's your support email for escalations?" → bot is live ✅
- Questions 5+ (Sentry, KYC, custom style) → appear as optional suggestions over next days

**Design rules:**
- URL parsing runs in background while next question is asked — no loading screens
- Live preview inline after URL submitted — tenant can ask bot a question mid-onboarding
- Every question has "Skip, I'll set this up later"
- 4 questions max before bot is live

**Effort:** 3–4 days.

---

### [FI-AUTODESIGN] Auto-Brand Widget Matching
**Why:** Removes the #2 objection in demos: "will it look right on our site?"

**What it does:**
- When client submits docs URL → extract brand colors + fonts from their site
- Pre-style the widget to match
- Show "We matched your brand — does this look right?" (not "please configure")

**Implementation:**
- CSS variable extraction from client's site (80% accurate on standard sites)
- Fallback to manual picker if extraction fails
- Also useful in demo builder (auto-styles the demo bot)

**Effort:** 2–3 days.

---

### [FI-DEMO-BOTS] Public Demo Bots (Stripe, Cloudflare, etc.)
**Why:** SEO + social proof + product-led growth.
Per strategy: "a developer searching for Stripe API finds Chat9, gets a better answer than official docs search, understands the product instantly."

**Candidates (criteria: large public docs + technical audience + OpenAPI spec):**
- Stripe (OpenAPI spec → showcase curl generation)
- Cloudflare
- Twilio
- Supabase (OpenAPI spec)

**Each demo page:**
- Live bot built on their public docs
- Auto-refresh every 48h (uses FI-021 background embeddings)
- Legal disclaimer: "built on public docs, not affiliated with [Company]"
- Live stats panel (when we have data): conversation count, avg cost, most asked today
- CTA: "Want this for your own API docs? Start free →"
- SEO target: "stripe api chatbot", "stripe documentation assistant"

**Effort:** 2 days setup + ongoing maintenance (auto-refresh).
**Dependency:** FI-021 (background embeddings) must be done first.

---

### [FI-CTA] URL-First Primary CTA on Landing Page
**Why:** Per strategy: person sees result before deciding to register. Higher conversion.

**Change:**
- Current: "Start free trial" button
- New: Input field "Enter your documentation URL →" as main CTA

**Flow after URL submitted:**
- Parse docs (background) → show preview bot → ask to sign up to keep it
- Trial limits: 50 pages indexed, 20 questions, 3-day expiry
- Limits become conversion funnels: "You have 200 pages. Sign up to index all."
- Email gate: enter work email before demo activates (prevents abuse)

**Effort:** 2–3 days (frontend + backend demo builder).

---

### [FI-ROADMAP] Public Roadmap with Feature Voting
**Why:** Retains customers, attracts new (SEO), provides free research.
Per strategy: "customers who vote and see feature move to In Progress do not churn before it ships."

**Statuses:**
- 🔭 Under consideration — we're aware, not committed
- 🔜 Planned — committed to this quarter
- 🚧 In progress — in development now
- ✅ Shipped — done (with link to changelog)

**Rules:**
- Weight votes by plan tier (5 Enterprise votes > 200 free votes)
- Email voters when feature ships — highest-ROI retention touchpoint
- Never promise specific dates publicly (quarters only)
- Review monthly, not weekly

**Effort:** 2 days (can use Canny, Frill, or build simple custom version).

---

### [FI-021] Background Embeddings (Async)
- Sync embedding = timeout on large files
- Move to background task (FastAPI BackgroundTasks or Celery)
- **Dependency for FI-DEMO-BOTS**
- Effort: 2 days | Priority: P2

### [FI-039] Daily Summary Email — "Chat9 as a team member"
See full spec above (unchanged). Priority: P2.

### [FI-040] Client Analytics Dashboard
See full spec above (unchanged). Priority: P2.

### [FI-041] Status Page Integration
See full spec above (unchanged). Priority: P2 (becomes P1 for Growth tier launch).

### [FI-005] Greeting message in widget (RICE: 1440)
- Client sets `greeting_message` in settings
- Effort: 1 day | Priority: P2

### [FI-011 v2] Auto-generation of FAQ from tickets (RICE: 325)
Priority: P2.

### [FI-027] Ticketing systems integration (Zendesk, Intercom, Freshdesk)
- Level 1: import tickets → embeddings
- Level 2: escalation → auto-create ticket
- Level 3: live handoff
- Effort: 5–8 days (Level 1) | Priority: P2

### [FI-P2-MULTUPLOAD] Multiple file upload
Effort: 1 day | Priority: P2

### [FI-P2-SOFTDELETE] Soft-delete for documents with restore
Effort: 1 day | Priority: P2

### [FI-P2-CONFIRM-DELETE] Delete confirmation dialog
Effort: 2 hours | Priority: P2

---

## 🟡 P3 — Later

### [FI-LIVE-ANALYTICS-DEMO] Live Analytics Panel on Demo Pages
- Counter: conversations, cost per conversation, "Most asked today"
- WebSocket or 5-min polling
- **Only launch when real data exists** (50+ convos on demo bot)
- Transparent cost display = differentiation
- Priority: P3 (wait for demo bots to accumulate data)

### [FI-001] Telegram integration (RICE: 120)
Client enters Telegram Bot Token → webhook → `/chat`.

### [FI-003/004] Rate limiting per-user
Needed together with pricing plans.

### Stripe / pricing plans
See BACKLOG_MONETIZATION.md for updated model.

### [FI-P3-WIDGET-THEME] Widget theming (data attributes)
Priority: P3

### [FI-P3-LARGEPDF] Large PDF progress bar
Priority: P3

---

## 🧊 Long-term (P3+)

- **Conversation summaries** — GPT summary of each session
- **Analytics charts** — trend charts
- **MCP server** — Chat9 as Claude/Cursor data source
- **Multi-user / team** — multiple members per account
- **Repository intelligence** — connect GitHub repo for code-aware support (Pro tier)
- **Customer success hire** — first hire when 20+ paying customers (not sales — customer success)

---

## ✅ Implemented

| FI | What | PR |
|----|-----|-----|
| FI-015 | Email verification | #24 |
| FI-016 | Enforce verification | #26 |
| FI-017 | Brevo HTTP email | #25 |
| FI-018 | Token tracking | #27 |
| FI-014 | Admin metrics MVP | #22 |
| FI-010 | 👍/👎 + Review bad answers | #20, #21 |
| Chat logs | Inbox-style /logs | #19 |
| Review debug | Retrieval debug in /review | — |
