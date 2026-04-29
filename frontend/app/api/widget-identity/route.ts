import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("chat9_token")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  // bot_id makes the backend sign the token with the bot owner's KYC secret
  // instead of the caller's own tenant's secret. Required for cross-tenant
  // widgets like the Chat9 dogfood support chat in the dashboard.
  const botId = request.nextUrl.searchParams.get("bot_id");
  const url = new URL(`${API_URL}/auth/me/widget-token`);
  if (botId) url.searchParams.set("bot_id", botId);

  try {
    const res = await fetch(url.toString(), {
      headers: { Authorization: `Bearer ${token}` },
    });

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ detail: "Unavailable" }, { status: 503 });
  }
}
