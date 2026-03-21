# Chat9 Go-to-Market Strategy

Source: Chat9 Product Strategy (March 2026, internal)
Last updated: 2026-03-21

---

## The Core Problem (Honest Assessment)

The tech stack (RAG on pgvector + OpenAI embeddings + chat widget) is commodity.
Any developer can replicate it in a weekend for $150.

**This is fine.** Notion started as "a database with a nice UI." Stripe started as "API calls to process payments."
The question is never "can this be copied?" — it's "what makes it progressively harder to copy over time?"

---

## The Four Moats (in order of speed)

| # | Moat Type | Code | When it starts working |
|---|-----------|------|----------------------|
| 1 | Switching Cost | SC | Day 1 — as soon as client configures KYC + escalation |
| 2 | Data Accumulation | DA | Month 2–3 — cross-tenant Gap Analyzer benchmarks |
| 3 | Workflow Integration | WI | Month 3–6 — Sentry, Status Page, GitHub |
| 4 | Vertical Specialisation | VS | Month 6–12 — developer tooling vertical |

**Switching Cost builds fastest** — that's why KYC, escalation, and **disclosure (extended: topics, segments)** are P1; **FI-DISC v1** (tenant-wide response level) is already shipped — see `PROGRESS.md`.

---

## Window of Opportunity

**12–18 months** before either:
- Intercom/Zendesk absorbs these features into their platforms
- A well-funded v2 competitor emerges

Goal: be the product that is **expensive to leave** by then.

---

## Competitive Landscape

### Tier 1 (don't compete yet)
Intercom, Zendesk, Freshdesk — enterprise, $300+/mo, full support suite

### Tier 2 (watch)
SiteGPT ($39–259/mo), Chatbase (10k+ users) — growing fast

### Tier 3 (compete here now)
CustomGPT, DocsBot ($19–99/mo) — horizontal RAG widgets
**Gap they don't fill:** KYC (you ship v1), escalation loops, error tracking, **full disclosure spec** (topic denylist, etc. beyond FI-DISC v1), gap analysis

**Tier 3 is winning deals that should go to a better product.**

---

## The First 10 Customers

**Not through ads or SEO.** Through direct outreach.

### Where to find them
- Communities: Indie Hackers, SaaStr, ProductLed Slack, MicroConf Slack
- Direct: find companies with public docs + visible "contact support" button
- Networks: CDNvideo, Birdview (warm intros convert better than cold)

### Target profile
- 10–50 employees
- Technical product (API, developer tool, SaaS)
- No dedicated support team
- Public documentation

### The Personalized Demo Tactic (highest-converting sales move)
Spend 15 minutes before any meeting building a bot on their public docs.
Walk into the meeting with their product already running.

**How to run it:**
1. Open with a question: "What do your users ask most in support?"
2. Type their answer into their bot live on screen
3. Show the Gap Analyzer: "We noticed you have no page on X — that's a common question"
4. Show one question the bot CAN'T answer → demonstrate escalation flow
   *(Showing a weakness before they find it builds more trust than a perfect demo)*

**Layer 2 — auto-brand the demo:**
Extract brand colors + fonts from their site → pre-style the widget to match.
Client sees widget that visibly belongs on their site → closes "will it look right?" objection.

---

## Positioning

### Current (not specific enough)
"Your Support Mate, Always On" — friendly, but doesn't win comparisons.

### By buyer type

**For the technical founder:**
> "Chat9 turns your API documentation into a support agent that knows when to escalate, never exposes things it shouldn't, and improves from every conversation it can't answer."

**For the PM:**
> "Your support inbox fills up with the same 20 questions. Chat9 answers them automatically, flags the ones it can't, and tells you which documentation pages to write next."

**For the ops/CX lead:**
> "Your Intercom bill is $300/month. Chat9 handles 80% of tier-1 queries for $19, escalates the rest to your existing tools, and learns from every escalation."

---

## Content Marketing

### The Gap Analyzer Wedge
A blog post: **"We analysed 50 SaaS documentation sites — here are the most common unanswered questions"**

- Data-driven, genuinely interesting
- Directly demonstrates the product's unique capability
- Attracts PMs and founders thinking about documentation quality
- Exact audience Chat9 wants to reach

### Integration-Led Growth
Every integration = a distribution channel.
When Chat9 appears in Sentry marketplace or Statuspage partner directory → reaches tenants already looking for support tooling.

---

## Demo Bots (Public)

Build working bots on well-known developer products and publish on Chat9 website.

### Priority candidates
| Service | Why |
|---------|-----|
| Stripe | OpenAPI spec → showcase curl generation; massive developer audience |
| Cloudflare | Complex docs, high developer traffic |
| Twilio | API docs, common search queries |
| Supabase | OpenAPI spec, Chat9's own stack user |

### Requirements
- Auto-refresh every 48h (depends on FI-021 background embeddings)
- Legal disclaimer: "built on public docs, not affiliated with [Company]"
- Link to "Build your own →" on every demo page
- SEO targets: "stripe api chatbot", "stripe documentation assistant", etc.

### Live Stats Panel (when ready)
Add only after 50+ conversations on demo bot:
- Conversation counter (live)
- Cost per conversation (~$0.0004)
- "Most asked today" list
- "Hours saved" estimate

---

## Landing Page CTA (Revised)

**Current:** "Start free trial" button
**New:** Input field as primary CTA:

```
"Enter your documentation URL"
[________________________] [Try it →]
```

**Flow:**
1. URL submitted → background parse starts
2. Preview bot shown inline → user can ask it questions
3. Results page → "Sign up to keep your bot + index all N pages"

**Trial limits (conversion funnels):**
- 50 pages indexed (shows value, hits limit on real docs)
- 20 questions (enough to see quality)
- 3-day expiry ("Your demo expires in 2 days")
- Email gate before activation (work email only — prevents abuse + creates lead)

---

## Onboarding: The Aha-Moment

**Activation moment:** URL parser — from "this will take hours" to "it took 4 minutes"

**Problem:** This is activation, not retention. It answers "does this work?" not "do I still need this in 30 days?"

**Solution:** Use the fast start to deliver the first Gap Analyzer insight immediately.

```
Minute 0:00 — Client submits docs URL
Minute 0:04 — Bot is live AND shows:
              "You have no page on Authentication. Products like yours
               get 23% of questions about this topic."
```

This is the real aha-moment: **new information before first user ever opens the widget.**

### The 4-Question Conversational Setup

Instead of settings forms → the onboarding IS a dialogue with the bot:

1. "What's your product and what does it do?" (1 sentence)
2. "Paste your docs URL" → parsing starts in background
3. "What should the bot say when it can't answer?"
4. "What's your support email for escalations?" → **bot is live**

Questions 5+ (KYC settings, Sentry, custom style) = optional suggestions over next days, never mandatory.

---

## The 12-Month Picture

| Month | Goal |
|-------|------|
| 1–2 | First 10 customers (free), prove value |
| 3 | Launch Starter → Growth pricing, KYC + escalation live |
| 4–6 | 50 customers, Gap Analyzer v2 (cross-tenant), Status Page integration |
| 6–9 | Sentry + GitHub integrations (Pro tier), integration-led growth |
| 9–12 | 200+ customers, consider first hire (customer success, not sales) |

---

## First Hire

**Customer success, not sales.**

A person whose job is to help the first 20 customers get maximum value.
Every hour understanding why a customer churns or upgrades = worth 10 hours of cold outreach.

---

## The One Thing

> The $150 and 3 days were not wasted. They produced a working product, a real understanding of the technical stack, and the credibility to sell to technical buyers. That is the foundation. What gets built on top of it in the next 12 months determines whether Chat9 is a project or a business.
