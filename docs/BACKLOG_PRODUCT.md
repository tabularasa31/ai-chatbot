# Product Features Backlog

Product features for clients and platform operators.
RICE prioritization — in `PRODUCT_BACKLOG.md`.

---

## 🟢 ✅ COMPLETED (2026-03-19)

### [FI-033] Switch to gpt-4o-mini ✅ DONE
- **Model updated:** gpt-3.5-turbo → gpt-4o-mini
- **System prompt:** Optimized for new model
- **Tests:** Updated and passing (135 tests)
- **Documentation:** Updated (03-tech-stack, 01-overview, 04-phase-breakdown, BACKLOG_TECH_DEBT)
- **Cost:** Same (~$0.15 per 1M input tokens)
- **Quality:** Significantly better for reasoning & multilingual
- **Status:** Deployed to production ✅

### [FI-035] Landing Page ✅ DONE
- **Domain:** getchat9.live/ (live & public)
- **Design:** Dark modern, fully responsive (mobile-first)
- **Stack:** Next.js, TailwindCSS, Figma → React
- **Sections:** Hero, Features (4), Demo widget, Stats, CTA, Footer
- **CTA wiring:** All "Try for free" buttons → `/signup`
- **Features:**
  - Demo widget with real Chat9 (needs API key config)
  - Animated hero section
  - Responsive navigation
  - All animations smooth (framer-motion)
- **Build:** 0 errors, 0 ESLint warnings
- **Status:** Production-ready ✅

### [SECURITY] Protect /review endpoint ✅ DONE
- **Issue:** `/review` was accessible without auth (contains sensitive client data)
- **Fix:** Added `/review` to PROTECTED_PATHS in middleware
- **What /review shows:** Bad answers, client feedback, debug retrieval info
- **Status:** Enforced via middleware ✅

### [SECURITY] CORS Configuration ✅ DONE
- **Was:** `allow_origins=["*"]` (insecure)
- **Now:** Whitelist via `CORS_ALLOWED_ORIGINS` env var
- **Implementation:** Robust parsing with `.strip()` + filtering
- **Methods:** GET, POST, PUT, DELETE, OPTIONS (restricted)
- **Headers:** Content-Type, Authorization (restricted)
- **Dev config:** localhost:3000, getchat9.live
- **Prod config:** getchat9.live (set on Railway)
- **Status:** Production-ready ✅

---

## 🔴 P1 — Doing now

### [FI-005] Greeting message in widget (RICE: 1440)
- Client sets `greeting_message` in settings.
- Widget shows it first on open (assistant role).
- If not set → default template.
- **Effort:** 1 day.

### [FI-007] Per-client system prompt (RICE: 1020)
- Client configures bot personality in dashboard.
- Different bots for different clients.
- *(details in BACKLOG_RAG_QUALITY.md)*

---

## 🔴 P1 — Quick wins

### [FI-038] "Powered by Chat9" in widget footer
**Idea:** Small "Powered by Chat9" line with link to getchat9.live at the bottom of each widget.

**Why it matters:**
- Each embedded widget = Chat9 advertising on client's site.
- Works like "Sent from iPhone" — passive viral marketing.
- Free for us, minimal cost for client.

**Implementation:**
- Add to `backend/widget/static/embed.js` at bottom of chat window:
  ```html
  <div style="...">
    Powered by <a href="https://getchat9.live" target="_blank">Chat9</a>
  </div>
  ```
- Style: small gray text, doesn't distract from chat.
- Future Premium can remove ("Remove branding").

**Effort:** 30 minutes.

---

### [FI-040] Client Analytics Dashboard
**Idea:** Simple analytics for client right in dashboard — not charts for charts' sake, but concrete insights on how the bot performs.

**Concept (compact widget on main page):**
```
This week:
📊 47 sessions  ·  143 messages  ·  avg 3.0 msg/session
🔢 12,450 tokens  ·  ~$0.04 (gpt-4o-mini)
🔝 Top topics: CORS (12), Live stream (8), API limits (6)
⚠️  3 unanswered questions
```

**Metrics for client:**
- Sessions per period (day / week / month)
- Unique users (by session_id)
- Avg messages per session
- **Tokens used** (sum Chat.tokens_used per period)
- **Approximate cost** ($) — auto-calculated from known gpt-4o-mini pricing
- Top 5 topics (question clustering via GPT)
- % unanswered questions (fallback rate)
- % with 👎 (bad answers)

**Why it matters:**
- Client sees real bot value in numbers.
- Highlights documentation gaps (top unanswered questions).
- Standard among competitors (Tidio, DocsBot, SiteGPT).
- Tied to Daily Summary Email (FI-039) — same data.

**Implementation:**
- Backend: aggregation over `Chat` and `Message` per period, topic clustering via GPT.
- Frontend: widget on dashboard main page + separate `/analytics` page.

**Effort:** 3–4 days.
**Priority:** P2.

---

### [FI-039] Daily Summary Email — "Chat9 as a team member"
**Idea:** Every morning account owner gets an email report from Chat9 about yesterday — as if the bot is reporting as a support team member.

**Email structure:**
```
Chat9 Daily Report — [Client name] — [Date]

Yesterday I answered N questions from your users.
Tokens used: 4,230 (~$0.01)

Most asked about:
- [topic 1] — X questions
- [topic 2] — Y questions

Where I couldn't help (N questions):
- "[question]" — no info in documentation
- "[question]" — found partial answer

Recommend adding to documentation:
- [topic 1]
- [topic 2]

See you tomorrow,
Chat9
```

**Why it matters:**
- Changes product perception: bot → "team member".
- Automatically highlights documentation gaps.
- Client sees value every day — even without opening dashboard.
- Differentiator — competitors (DocsBot, SiteGPT) don't have this.

**Technical:**
- Cron job once a day (morning in client timezone).
- GPT analyzes yesterday's sessions → generates report.
- Send via Brevo (already configured).
- Settings: on/off in dashboard, send time.

**Effort:** 2–3 days.
**Priority:** P2 — after basic features, but before Zendesk integration.

---

### [FI-041] Status Page Integration (from Elina's spec)
**Idea:** Integrate real-time service status (Statuspage.io, Instatus, Freshstatus) into the bot.

When user asks "why is my API broken?" during an incident → bot instantly answers:
```
⚠️ There's an active incident affecting the API.
Started: 14:23 UTC  |  Status: Investigating
Latest: Engineers identified root cause, deploying fix. ETA 30min
Learn more: https://status.yourproduct.com
```

**Why it matters:**
- Differentiator — competitors lack real-time incident awareness
- Reduces support tickets by 50%+ during incidents
- Viral value — people check status more often via bot
- Potential premium feature

**Technical:**
- Polling worker every 60 sec (Celery / FastAPI background tasks)
- Redis cache with TTL 90 sec
- Query-time relevance check: show incident only if relevant to question
- Webhook support for Statuspage.io
- Component-to-topic mapping in tenant dashboard

**Effort:** 5–6 days (polling + caching + relevance + dashboard + tests)

**Priority:** P2 (after gpt-4o-mini and email verification)

**Spec:** See `docs/FEATURE_SPECS_REVIEW.md` and source `status-page-spec.docx`

---

## 🟠 P2 — Next sprint

### [FI-009] Improved chunking + metadata (RICE: 420)
- Overlap + structural chunking.
- *(details in BACKLOG_RAG_QUALITY.md)*

### [FI-011 v2] Auto-generation of FAQ from tickets (RICE: 325)
- Not manual input — auto-generation from uploaded tickets.
- Client approves/rejects suggested Q&A pairs.
- USP: "Upload tickets → we'll build FAQ for you."

### [FI-027] Ticketing systems integration (Zendesk, Intercom, Freshdesk)
- Level 1: import tickets → embeddings (auto-sync).
- Level 2: escalation → auto-create ticket if bot doesn't know.
- Level 3: live handoff to agent.
- **Key for Western market.**
- **Effort:** 5–8 days (Level 1).

### [FI-014] Admin metrics (already implemented ✅)
- Summary + per-client table.
- Tokens, sessions, documents, OpenAI key status.

### [FI-012] Admin dashboard (operator view)
- Extended: global logs, % bad answers per client.
- Do after data accumulates.

---

## 🟡 P3 — Later

### [FI-001] Telegram integration (RICE: 120)
- Client enters Telegram Bot Token → webhook → our `/chat`.

### [FI-003/004] Rate limiting per-user
- Needed together with pricing plans (Stripe).

### Stripe / pricing plans (RICE: 206)
- Free / Premium tiers.
- Limits on requests, documents, tokens.

---

## 🧊 Long-term backlog (P3+, when the time comes)

- **Conversation summaries** — GPT summary of each session in logs. Useful when 50+ sessions/day.
- **Analytics charts** — trend charts by questions, topics, resolution rate.
- **MCP server** — connect Chat9 as data source for Claude/Cursor via Model Context Protocol.
- **Multi-user / team** — multiple team members in one account.
- **Custom widget design** — custom colors, fonts, logo in widget (see BACKLOG_EMBED-PHASE2.md).

---

## Added from Grok Review (2026-03-19)

### [FI-P2-MULTUPLOAD] Multiple file upload
- Currently: one file at a time.
- Add multi-select + bulk upload with progress.
- **Effort:** 1 day | **Priority:** P2

### [FI-P2-SOFTDELETE] Soft-delete for documents with restore
- Currently: hard delete (no undo).
- Add `deleted_at` + trash view + restore option.
- Prevents accidental data loss.
- **Effort:** 1 day | **Priority:** P2

### [FI-P2-CONFIRM-DELETE] Delete confirmation dialog
- Add confirmation modal before deleting docs/bots.
- Simple UX improvement, prevents accidents.
- **Effort:** 2 hours | **Priority:** P2

### [FI-P3-WIDGET-THEME] Widget theming (data attributes)
- `data-theme="dark|light"`, `data-position="left|right"`, `data-color="#007bff"`
- Already tracked in BACKLOG_EMBED-PHASE2.md
- **Priority:** P3

### [FI-P3-LARGEPDF] Large PDF progress bar
- PDFs >50 pages cause slow embedding with no feedback.
- Add progress indicator + background task.
- **Effort:** 1-2 days | **Priority:** P3

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
