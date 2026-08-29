import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

// Cursor poll for the widget: everything written after `after_message_id`.
// Separate from /widget/history, which bootstraps the conversation on mount —
// this one is called every few seconds while a human is answering, so it stays
// as thin as the proxy can make it.
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("bot_id") ?? searchParams.get("botId");
  const sessionId = searchParams.get("session_id");
  const afterMessageId = searchParams.get("after_message_id");

  if (!botId || !sessionId) {
    return NextResponse.json(
      { detail: "bot_id and session_id are required" },
      { status: 400 },
    );
  }

  const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
  if (afterMessageId) params.set("after_message_id", afterMessageId);
  const res = await fetch(`${API_URL}/widget/messages?${params}`);
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
