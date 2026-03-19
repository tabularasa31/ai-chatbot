# Security Backlog

Security, isolation, abuse protection.

---

## 🟠 P2

### [FI-022] CORS — split by routes
- `allow_origins=["*"]` only for `/chat` and `/embed.js`.
- Rest — restrict to `FRONTEND_URL`.

### [FI-022 ext] CORS with client domain whitelist
- Client specifies `allowed_origins` in dashboard.
- Backend checks `Origin` against `Client.allowed_origins` on `/chat`.
- Protection against API key use on third-party sites.
- **Effort:** 2 days.

### [FI-023] Rate limit on `GET /clients/validate/{api_key}`
- Public endpoint without rate limit → brute-force possible.
- Add `@limiter.limit("20/minute")`.

### [FI-035] Prompt injection protection
- Sanitize incoming messages.
- Check for role-switch attempts ("ignore previous instructions...").

---

## 🟡 P3

### [FI-006] ENCRYPTION_KEY rotation
- Secure update of OpenAI keys encryption master key.
- Procedure: decrypt old → encrypt new → no data loss.
