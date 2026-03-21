# Monetization Backlog

Last updated: 2026-03-21
Based on: Chat9 Product Strategy (March 2026)

---

## Current Status
**Don't charge yet** — focus on product-market fit and first 10 customers.
Build the product that's expensive to leave, then monetize.

**Shipped (product, not billing):** Widget **KYC v1** (HMAC identity token, `POST /widget/session/init`, dashboard signing secret) — 2026-03-21. **FI-DISC v1** (tenant-wide response level, Response controls UI) — 2026-03-21. Escalation / gap analyzer / further disclosure (topic blocklist, segments) remain on the roadmap as listed below.

---

## Pricing Model (Revised per Strategy)

**Core principle:** Charge for what actually costs us money.
- Conversations cost Chat9 **nothing** (client pays OpenAI directly)
- Real costs: pgvector storage (grows with document volume) + integration maintenance
- → Price axis: **pages in index** (transparent, countable by client)

### One unit = one URL or one file up to 50,000 words
(Larger files split automatically)

---

## Pricing Tiers

| Feature | Starter (free) | Growth ($19/mo) | Pro ($49/mo) |
|---------|---------------|-----------------|--------------|
| Pages in index | 50 pages | 500 pages | 5,000 pages |
| Conversations | Unlimited | Unlimited | Unlimited |
| Bots | 1 | 5 | Unlimited |
| KYC (user identification) | ✅ | ✅ | ✅ |
| L2 Escalation tickets | ✅ | ✅ | ✅ |
| Disclosure Controls | ✅ | ✅ | ✅ |
| Gap Analyzer | ✅ | ✅ | ✅ |
| "Powered by Chat9" | visible | visible | removable |
| Status Page integration | ❌ | ✅ | ✅ |
| Sentry / error tracking | ❌ | ❌ | ✅ |
| Repository intelligence | ❌ | ❌ | ✅ |
| Priority support | ❌ | ❌ | ✅ |

---

## Key Pricing Principles (from strategy)

**1. Gap Analyzer on all plans**
It's the #1 differentiator. Hiding it behind paywall hides the main purchase argument.
Customers who see it will upgrade. Customers who don't see it won't.

**2. Unlimited conversations on all plans**
Client pays OpenAI directly. Charging for conversations = double-billing with no justification.
"Unlimited conversations" is both honest and a marketing advantage vs competitors.

**3. Core features on all plans**
KYC, escalation, disclosure (extended), gap analyzer = baseline for production-ready bot. (Disclosure **v1** level control is shipped; see `PROGRESS.md`.)
"A bot that can't identify users, can't escalate, can't control what it reveals is not production-ready."

**4. Integrations gate the tiers**
External integrations (Status Page, Sentry, GitHub) have highest switching cost.
Deeper integration = harder to leave. This is where Growth/Pro justify their price.

---

## The Limit-as-Funnel Strategy (demo builder)

Trial limits are conversion mechanisms:
- "Your docs have 200 pages. The demo indexed 50. Sign up to index everything."
- "You've asked 20 questions. Upgrade to continue."
- "Your demo expires in 2 days. Keep it live — upgrade to Starter."

---

## Old Models (archived, for reference)

### Option A: By sessions
- Free: 500/mo | Starter $19: 2000/mo | Pro $49: unlimited

### Option B: By domains
- Free: 1 domain | Pro $29: 5 domains | Business $99: unlimited

### Option C: Flat monthly
- Free limited | Pro $29: all included

---

## Roadmap

**Now:** Build product, get first customers for free, prove value.
**Month 3–4:** Launch Starter → Growth pricing.
**Month 6+:** Pro tier with deep integrations.
**When to charge:** When a customer voluntarily says "this saves us X hours/week."

---

## Notes on Competitors

| Competitor | Price | What we beat |
|-----------|-------|-------------|
| DocsBot | $19–99/mo | Unbranded widget: DocsBot = $99/mo; we include in Pro |
| SiteGPT | $39–259/mo | Cheaper, transparent pricing |
| Chatbase | 10k+ users | Feedback loop, gap analyzer, daily email |
| Intercom | $300+/mo | Fraction of the price for the same AI features |
