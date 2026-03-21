# BACKLOG: FI-EMBED Phase 2 & 3 Features

**Related:** FI-EMBED-MVP (Phase 1, done in 2-3 days)

This backlog contains features that are nice-to-have but not blocking the initial MVP release.

**Status (2026-03-21):** Baseline **slowapi** limits are already in production: `/widget/session/init` and `/widget/chat` (20/min), plus app-wide limits on `/clients/validate/{api_key}`, `/search`, `/chat` (see `PROGRESS.md`, `backend/core/limiter.py`). Items below describe **additional** hardening (per-client global caps, daily quotas, tighter per-IP policy), not “add any limiter from zero.”

---

## Phase 2: Robustness & Performance (1-2 weeks)

### FI-EMBED-RATE-LIMIT: Rate limiting — **next tier** (was: “no limits”)

**Problem (remaining):** Current limits are uniform; a distributed or multi-key abuse pattern can still burn OpenAI budget. No **daily quota** or **per-tenant** cap on widget traffic.

**Solution (incremental):**
- Optional stricter **per-IP** policy where it differs from today’s slowapi keys
- **Per-clientId global** limit (e.g. 1000 req/min) across all callers
- **Daily quota** per client (free: 100, pro: 10k, enterprise: unlimited) — needs billing/subscription model

**Effort:** 1-2 days (on top of existing limiter)

**Files:**
- `backend/core/limiter.py` (tiered keys / config)
- `backend/routes/widget.py` (extend decorators or middleware)
- Tests for new caps

---

### FI-EMBED-ERROR-HANDLING: Error Handling & Mobile Responsiveness

**Problem:** embed.js is fragile (no timeout, no mobile scaling, no error messages).

**Solution:**
- Add onerror handler to iframe
- Add 5-second timeout fallback
- Responsive sizing (scales to mobile viewport)
- document.currentScript + fallback
- Sandbox attribute restrictions

**Effort:** 1 day

**Files:**
- `backend/static/embed.js` (rewrite with error handling)

**Related:** Claude review feedback (5 points)

---

### FI-EMBED-MOBILE: Mobile Widget Optimization

**Problem:** Fixed 400×600 breaks on phones.

**Solution:**
- Responsive iframe (max-width: 100%)
- Dynamic height (up to viewport)
- Bottom sheet for mobile (not sidebar)
- data-width, data-height, data-position support

**Effort:** 1-2 days

**Files:**
- `backend/static/embed.js`
- `frontend/components/ChatWidget.tsx`

---

### FI-EMBED-CSP-DOCS: Content Security Policy Documentation

**Problem:** Customers with strict CSP can't load script.

**Solution:**
- Create `docs/CUSTOMER_CSP_GUIDE.md`
- Generate CSP headers example in dashboard
- Recommend: script-src, frame-src, connect-src

**Effort:** 0.5 days

---

### FI-EMBED-VERSIONING: Script Versioning & Caching

**Problem:** Updating embed.js breaks old installations.

**Solution:**
- /embed.js?v=1, v=2, v=latest
- Long-term caching with version in URL
- Backward compatibility for old versions (with deprecation notice)

**Effort:** 1 day

**Files:**
- `backend/routes/public.py` (add version handling)

---

### FI-EMBED-SUBSCRIPTION-CHECKS: Subscription Status Validation

**Problem:** Suspended/cancelled accounts can still use widget.

**Solution:**
- Check `subscription_status` in /widget/chat
- Return 403 with specific reason (payment issue, cancelled)
- Track usage per billing period

**Effort:** 0.5 days

**Files:**
- `backend/routes/widget.py` (add status checks)

---

## Phase 3: Advanced Features (2-4 weeks)

### FI-EMBED-CUSTOMIZATION: Widget Customization

**Problem:** All customers want custom colors, fonts, positioning.

**Solution:**
- Dashboard form: pick colors, position, size
- Store in Client table (embed_config JSON)
- Pass to iframe via URL params
- embed.js applies CSS dynamically

**Effort:** 2-3 days

**Files:**
- `backend/models.py` (add embed_config column)
- `backend/routes/public.py` (return config)
- `backend/static/embed.js` (apply CSS)
- `frontend/app/dashboard/[clientId]/customize/page.tsx` (new UI)

---

### FI-EMBED-DOMAIN-RESTRICTIONS: Optional Domain Restrictions

**Problem:** Enterprise customers want to limit widget to specific domains.

**Solution:**
- Client.embed_allowed_origins (optional)
- Check origin in /widget/chat
- Return 403 if domain not whitelisted
- Off by default (any domain works)

**Effort:** 1 day

**Files:**
- `backend/models.py` (add column)
- `backend/routes/widget.py` (add origin check)
- Migration

---

### FI-EMBED-ANALYTICS: Widget Usage Analytics

**Problem:** Customers can't see how popular their widget is.

**Solution:**
- Track widget loads (embed.js loads)
- Track unique domains per clientId
- Track message volume per day
- Dashboard widget with metrics

**Effort:** 2-3 days

**Files:**
- `backend/models.py` (add WidgetMetric table)
- `backend/routes/widget.py` (log events)
- `frontend/app/dashboard/[clientId]/analytics/page.tsx` (new dashboard)

---

### FI-EMBED-REFERRER-TRACKING: Referrer & UTM Tracking

**Problem:** Customers don't know which of their pages send the most traffic.

**Solution:**
- Capture document.referrer in embed.js
- Log referrer domain for each request
- Dashboard shows top referrers
- Support UTM parameters (utm_source, utm_medium, utm_campaign)

**Effort:** 1-2 days

**Files:**
- `backend/models.py` (add referrer to Message)
- `backend/static/embed.js` (send referrer)
- `frontend/app/dashboard/[clientId]/analytics/page.tsx` (visualize)

---

### FI-EMBED-GDPR: GDPR & Privacy Compliance

**Problem:** EU customers need GDPR consent + data export.

**Solution:**
- Respect window.DO_NOT_TRACK
- Add Consent Mode integration (Google, Cloudflare)
- GDPR data export endpoint
- Privacy policy template

**Effort:** 2 days

**Files:**
- `backend/routes/widget.py` (check DO_NOT_TRACK header)
- `backend/api/privacy.py` (data export endpoint)
- `backend/static/embed.js` (consent integration)
- Docs

---

### FI-EMBED-TESTING: E2E Testing & Browser Compatibility

**Problem:** No E2E tests for full embed flow.

**Solution:**
- E2E test: create bot → copy code → paste → verify works
- Test on Chrome, Firefox, Safari, Edge
- Test on iOS Safari, Chrome Mobile
- Test with CSP headers

**Effort:** 1-2 days

**Files:**
- `tests/e2e/embed.spec.ts` (Playwright)

---

### FI-EMBED-MONITORING: Production Monitoring & Alerts

**Problem:** Can't detect outages or anomalies.

**Solution:**
- Monitor /embed.js load count (should be stable)
- Alert on error spikes (validation errors, timeouts)
- Alert on unusual traffic patterns (10x normal)
- Dashboard: real-time metrics

**Effort:** 1-2 days

**Files:**
- `backend/monitoring/embed_metrics.py`
- Alert configuration

---

## Suggested Order (After MVP)

1. **Immediately (Week 2):**
   - **Tier-2** rate limits / quotas if metrics show abuse (baseline limits already shipped)
   - Error handling + mobile (improves UX)
   - CSP docs (unblocks enterprise customers with strict CSP)

2. **Soon (Week 3):**
   - Versioning (allows safe updates)
   - Subscription checks (prevents freeloaders)
   - Domain restrictions (enterprise feature)

3. **Later (Month 2):**
   - Customization (nice-to-have)
   - Analytics (marketing feature)
   - GDPR (compliance)
   - E2E testing (reliability)

---

## Priority Scoring (RICE)

| Feature | Reach | Impact | Confidence | Effort | Score |
|---------|-------|--------|------------|--------|-------|
| Rate limiting (tier 2: quotas / per-client global) | High | High | High | Medium | 10 |
| Error Handling | Medium | High | High | Low | 6 |
| Mobile Responsive | High | Medium | High | Medium | 6 |
| CSP Docs | Medium | Medium | High | Low | 4 |
| Customization | High | Medium | Medium | High | 2 |
| Analytics | Medium | Medium | Medium | Medium | 2 |
| GDPR | Low | High | High | Medium | 3 |
| Versioning | Medium | High | Medium | Low | 3 |

**Top priorities:** Error handling (embed.js) > Mobile > CSP docs > **tier-2** rate limits (quotas / per-client global)

---

## Notes

- These are all **optional for MVP**
- Baseline widget + API rate limits are **shipped**; prioritize **embed robustness** and **mobile** next unless abuse metrics demand quotas sooner.
- Phase 3 can wait **1-2 months** (nice-to-have features)
- Review this backlog quarterly or after incident reports

---

_Last updated: 2026-03-21_
