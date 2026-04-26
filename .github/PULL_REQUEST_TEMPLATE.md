## Summary

<!-- What does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / chore
- [ ] Docs / config

## Checklist

- [ ] Tests added or updated where applicable
- [ ] `make smoke` passes locally
- [ ] Frontend lint passes (`cd frontend && npm run lint`)

### Cookie / auth / CORS changes

> Complete this section only if the PR touches cookies, CORS headers, auth flow, or `credentials` settings.

- [ ] Tested on a **Vercel preview** hitting the **Railway backend** (not just localhost)
- [ ] No raw `fetch()` to `${BASE_URL}` added outside `frontend/lib/api.ts` — all calls go through `apiFetch()`
- [ ] `Set-Cookie` behaviour verified in DevTools (Network → response headers) on the preview URL
