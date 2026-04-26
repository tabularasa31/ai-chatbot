import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PROTECTED_PATHS = [
  "/dashboard",
  "/knowledge",
  "/settings",
  "/widget-settings",
  "/logs",
  "/review",
  "/debug",
  "/admin",
  "/escalations",
  "/gap-analyzer",
];
const AUTH_PATHS = ["/login", "/signup"];

export function middleware(request: NextRequest) {
  const token = request.cookies.get("chat9_token")?.value;
  const sessionMarker = request.cookies.get("chat9_session")?.value;
  const hasSession = Boolean(token || sessionMarker);
  const { pathname } = request.nextUrl;

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
    "/debug",
    "/debug/:path*",
    "/admin",
    "/admin/:path*",
    "/escalations",
    "/escalations/:path*",
    "/gap-analyzer",
    "/gap-analyzer/:path*",
    "/login",
    "/signup",
  ],
};
