# Market Research: AI Support Bot / Doc-based Chatbot SaaS

Market and competitive landscape research for Chat9.
Date: 2026-03-18.

---

## Market

### Category
"Doc-based AI chatbot" — a chatbot that learns from client documentation and answers user questions. Closest parent category: AI Customer Support / Live Chat.

### Market size
- AI Customer Service market is growing actively in 2025–2026.
- Major players (Intercom Fin, Tidio) already have 10k+ clients.
- Specialized niche "docs-trained chatbot" (Chatbase, DocsBot) — fast-growing segment.
- Chat9 ICP: small and mid-size B2B SaaS, API/tech products — huge market.

---

## Competitive landscape

### Segment 1: Enterprise AI Support Agents

**Fin by Intercom** (fin.ai)
- Positioning: #1 AI Agent for customer service.
- Price: **$0.99 per resolved conversation**.
- Integrations: any helpdesk (Zendesk, Salesforce, HubSpot).
- Resolution rate: ~65% end-to-end.
- Target audience: mid and large business.
- **Vs Chat9:** incomparably more expensive, requires helpdesk, complex onboarding.

**DataDome**
- Positioning: Enterprise bot protection + fraud prevention.
- Price: $1000+/mo.
- Target audience: large e-commerce and media.
- **Vs Chat9:** different niche (bot protection, not support).

---

### Segment 2: SMB Live Chat + AI

**Tidio** (tidio.com)
- Positioning: AI + human customer service platform.
- Prices:
  - Starter: **$24/mo** (100 conversations)
  - Growth: **from $49/mo** (250+ conversations)
  - Plus: **from $749/mo** (custom)
- Features: live chat, tickets, AI bot (Lyro), Zendesk/Salesforce integration.
- Target audience: e-commerce, SMB.
- **Vs Chat9:** Tidio is a general live chat platform, not RAG on docs. More expensive at scale.

**Crisp** (crisp.chat)
- Positioning: flat rate per workspace, all included.
- Prices: not shown publicly, flat monthly.
- Features: live chat, AI, multichannel.
- **Vs Chat9:** similar to Tidio, general platform without focus on documentation.

---

### Segment 3: Doc-based Chatbot (direct competitors)

**Chatbase** (chatbase.co)
- Positioning: "Train ChatGPT on your data".
- Prices: not shown publicly, 10,000+ clients.
- Features: upload docs → chatbot → embed on site.
- **Vs Chat9:** closest competitor. No conversation logs, no feedback loop, no "your OpenAI key" model.

**DocsBot AI** (docsbot.ai)
- Positioning: AI chatbot trained on your documentation.
- Prices:
  - Free: 1 bot, 50 pages, 100 messages/mo.
  - Personal: **$19/mo** — 3 bots, 5k pages, 5k messages.
  - Standard: **$49/mo** (most popular) — 10 bots, 15k pages, 15k messages.
  - Business: **$99/mo** — 100 bots, 100k pages, unbranded widget.
- Features: Help Scout integration, analytics, conversation summaries, MCP server.
- **Vs Chat9:** very close competitor. Stronger on integrations. Weaker on RAG quality controls (no our 👍/👎 pipeline, no debug mode).
- **Important:** "Unbranded widget" only in Business ($99/mo) — our FI-038 is a differentiator.

**SiteGPT** (sitegpt.ai)
- Positioning: AI customer support agent from your website/docs.
- Prices:
  - Starter: **$39/mo**
  - Growth: **$79/mo**
  - Scale: **$259/mo**
  - Enterprise: custom
- Zendesk escalation supported.
- **Vs Chat9:** similar product, more expensive. No explicit feedback/quality loop.

---

## Chat9 positioning

### Market position

```
                  EXPENSIVE
                     │
Enterprise ──── Fin ($0.99/conv) ──── DataDome ($1k+)
                     │
         Tidio ($24–749) ─── Crisp
                     │
         SiteGPT ($39–259) ─── DocsBot ($19–99)
                     │
      ★ CHAT9 (free/freemium) ─── Chatbase
                     │
                  CHEAP
```

### Our differentiators

1. **"Your OpenAI key"** — you pay OpenAI directly, we don't markup tokens. DocsBot and SiteGPT include AI costs in subscription price (effectively — hidden markup).

2. **Feedback loop** (👍/👎 + ideal_answer + training data) — competitors don't have this. Path to self-improving bot.

3. **Debug mode** — see which chunks were used. Not available to direct competitors.

4. **"Powered by Chat9" / branding** — DocsBot charges $99/mo for unbranded widget. Ours will be a Premium feature.

5. **Simplicity** — 5 minutes to first answer. No complex helpdesk integrations for basic use case.

---

## Chat9 weaknesses vs competitors

| Aspect | Competitors | Chat9 now |
|--------|-------------|------------|
| Integrations (Zendesk, HubSpot) | DocsBot, SiteGPT, Fin | ❌ None (FI-027 on roadmap) |
| Multi-user / team | DocsBot, Tidio | ❌ None |
| Analytics / charts | DocsBot, Tidio | ❌ Basic metrics |
| Custom widget design | All | ❌ None |
| Number of bots | DocsBot: 3–100 | 1 bot = 1 account |
| Brand awareness | Chatbase: 10k+ clients | 🆕 New player |

---

## Conclusion: where Chat9 can win

**Now (before integrations):**
- Technical B2B teams that want control over AI costs (own key).
- Teams that want to understand answer quality (debug + feedback loop).
- Small SaaS with one product, one bot.

**Later (with Zendesk + multi-tenant + branding):**
- Compete with DocsBot and SiteGPT directly on price and features.
- Positioning: "DocsBot but with better quality controls and transparent pricing."

---

## Sources

- fin.ai (Intercom Fin) — direct site, March 2026
- tidio.com/pricing — direct site, March 2026
- docsbot.ai/pricing — direct site, March 2026
- sitegpt.ai/pricing — direct site, March 2026
- chatbase.co — direct site, March 2026
- crisp.chat/en/pricing — direct site, March 2026
