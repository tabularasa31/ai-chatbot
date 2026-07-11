// Server-only session helper: decodes the auth JWT straight from the request
// cookie so Server Components can read session claims without a round-trip to
// GET /auth/me. Importing `next/headers` makes this module server-only.
import { cookies } from "next/headers";
import { jwtVerify } from "jose";

// Must match the backend: cookie set in backend/auth/routes.py, claims built
// in backend/auth/service.py (HS256, typ from backend/core/jwt_kinds.py).
const AUTH_COOKIE_NAME = "chat9_token";
const USER_ACCESS_JWT_TYP = "chat9_user";

export interface SessionClaims {
  userId: string;
  /** Null for tokens issued before the email claim was added. */
  email: string | null;
}

/**
 * Read and verify the auth JWT from the request cookie.
 *
 * Returns null on any failure (no cookie, bad/expired token, JWT_SECRET not
 * configured on the frontend server) — callers fall back to client-side
 * `api.auth.getMe()`, so this degrades gracefully rather than breaking auth.
 */
export async function getSessionFromCookie(): Promise<SessionClaims | null> {
  const secret = process.env.JWT_SECRET;
  if (!secret) return null;

  const token = cookies().get(AUTH_COOKIE_NAME)?.value;
  if (!token) return null;

  try {
    const { payload } = await jwtVerify(
      token,
      new TextEncoder().encode(secret),
      { algorithms: ["HS256"] },
    );
    if (payload.typ !== USER_ACCESS_JWT_TYP) return null;
    if (typeof payload.sub !== "string" || !payload.sub) return null;
    return {
      userId: payload.sub,
      email: typeof payload.email === "string" ? payload.email : null,
    };
  } catch {
    return null;
  }
}
