import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PROTECTED_PATHS = [
  "/dashboard",
  "/knowledge",
  "/settings",
  "/widget-settings",
  "/logs",
  "/review",
  "/admin",
  "/escalations",
  "/gap-analyzer",
];
const AUTH_PATHS = ["/login", "/signup"];

// CORS allowlist for cross-origin requests from the standalone widget-app.
// The widget-app lives on a different origin (e.g. widget.chat9.live) and
// calls /widget/* and /api/widget-* on the dashboard origin.
// Identity flows via postMessage + Bearer token, so we never need
// Access-Control-Allow-Credentials and never accept cookies cross-origin.
const WIDGET_ALLOWED_ORIGINS = (process.env.WIDGET_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const WIDGET_CORS_PATH_PREFIXES = ["/widget/", "/api/widget-session/"];

function isWidgetCorsPath(pathname: string): boolean {
  return WIDGET_CORS_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function buildCorsHeaders(origin: string): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, authorization",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const origin = request.headers.get("origin");

  // CORS handling for widget cross-origin calls (runs before auth checks
  // because widget paths are not in PROTECTED_PATHS and need preflight to
  // succeed even for unauthenticated visitors).
  if (origin && isWidgetCorsPath(pathname)) {
    const allowed = WIDGET_ALLOWED_ORIGINS.includes(origin);

    if (request.method === "OPTIONS") {
      // Reject disallowed origins with an explicit 403 instead of letting
      // the request fall through to a CORS-headerless 200 — browsers would
      // block either way, but this makes loader/env misconfiguration loud
      // in dev tools instead of producing silent "fetch failed" errors.
      if (!allowed) {
        return new NextResponse(null, { status: 403 });
      }
      return new NextResponse(null, { status: 204, headers: buildCorsHeaders(origin) });
    }

    if (allowed) {
      const res = NextResponse.next();
      for (const [key, value] of Object.entries(buildCorsHeaders(origin))) {
        res.headers.set(key, value);
      }
      return res;
    }
    // Disallowed origin on a non-OPTIONS widget request: let the response
    // through without CORS headers; the browser will refuse to expose it
    // to the caller. Same behavior as before, just made explicit.
  }

  const token = request.cookies.get("chat9_token")?.value;
  const sessionMarker = request.cookies.get("chat9_session")?.value;
  const hasSession = Boolean(token || sessionMarker);

  const isProtected = PROTECTED_PATHS.some((p) => pathname.startsWith(p));
  const isAuth = AUTH_PATHS.some((p) => pathname.startsWith(p));

  if (isProtected && !hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (isAuth && hasSession) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard",
    "/dashboard/:path*",
    "/knowledge",
    "/knowledge/:path*",
    "/settings",
    "/settings/:path*",
    "/widget-settings",
    "/widget-settings/:path*",
    "/logs",
    "/logs/:path*",
    "/review",
    "/review/:path*",
    "/admin",
    "/admin/:path*",
    "/escalations",
    "/escalations/:path*",
    "/gap-analyzer",
    "/gap-analyzer/:path*",
    "/login",
    "/signup",
    // Widget CORS — must be in the matcher for middleware to run.
    "/widget/:path*",
    "/api/widget-session/:path*",
  ],
};
