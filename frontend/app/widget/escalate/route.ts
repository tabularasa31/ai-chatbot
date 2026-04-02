import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("botId") || searchParams.get("clientId");
  const sessionId = searchParams.get("session_id");

  if (!botId || !sessionId) {
    return NextResponse.json(
      { detail: "botId (or legacy clientId) and session_id are required" },
      { status: 400 }
    );
  }

  let body: { user_note?: string | null; trigger?: string } = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  const params = new URLSearchParams({ client_id: botId, session_id: sessionId });
  const res = await fetch(`${API_URL}/widget/escalate?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_note: body.user_note ?? null,
      trigger: body.trigger ?? "user_request",
    }),
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
