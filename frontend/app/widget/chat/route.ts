import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("botId") || searchParams.get("clientId");
  const message = searchParams.get("message");
  const sessionId = searchParams.get("session_id");
  const locale = searchParams.get("locale");
  const optionId = searchParams.get("option_id");

  if (!botId || !message) {
    return NextResponse.json(
      { detail: "botId (or legacy clientId) and message are required" },
      { status: 400 }
    );
  }

  const params = new URLSearchParams({ client_id: botId, message });
  if (sessionId) params.set("session_id", sessionId);
  if (locale) params.set("locale", locale);
  if (optionId) params.set("option_id", optionId);

  const res = await fetch(`${API_URL}/widget/chat?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
