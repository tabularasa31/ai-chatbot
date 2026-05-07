import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("botId");
  const sessionId = searchParams.get("session_id");

  if (!botId || !sessionId) {
    return NextResponse.json(
      { detail: "botId and session_id are required" },
      { status: 400 }
    );
  }

  let body: {
    user_note?: string | null;
    trigger?: string;
    failure_type?: string | null;
    original_user_message?: string | null;
  } = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  const params = new URLSearchParams({ bot_id: botId, session_id: sessionId });
  const res = await fetch(`${API_URL}/widget/escalate?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_note: body.user_note ?? null,
      trigger: body.trigger ?? "user_request",
      failure_type: body.failure_type ?? null,
      original_user_message: body.original_user_message ?? null,
    }),
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
