import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("botId");
  const message = searchParams.get("message");
  const sessionId = searchParams.get("session_id");
  const locale = searchParams.get("locale");

  if (!botId || message === null) {
    return NextResponse.json(
      { detail: "botId and message are required" },
      { status: 400 }
    );
  }

  const params = new URLSearchParams({ tenant_id: botId });
  if (sessionId) params.set("session_id", sessionId);
  if (locale) params.set("locale", locale);

  const res = await fetch(`${API_URL}/widget/chat?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
