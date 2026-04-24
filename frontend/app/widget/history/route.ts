import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("botId");
  const sessionId = searchParams.get("session_id");

  if (!botId || !sessionId) {
    return NextResponse.json(
      { detail: "botId and session_id are required" },
      { status: 400 },
    );
  }

  const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
  const res = await fetch(`${API_URL}/widget/history?${params}`);
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
