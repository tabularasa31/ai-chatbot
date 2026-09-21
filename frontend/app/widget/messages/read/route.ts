import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

// Read receipt for the widget: the visitor has had `message_id` on screen.
// Companion to the cursor poll next door; it is what decides whether an
// operator's reply gets mailed to the visitor after the grace period.
export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("bot_id") ?? searchParams.get("botId");
  const sessionId = searchParams.get("session_id");

  if (!botId || !sessionId) {
    return NextResponse.json(
      { detail: "bot_id and session_id are required" },
      { status: 400 },
    );
  }

  const body = await request.text();
  const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
  const res = await fetch(`${API_URL}/widget/messages/read?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
