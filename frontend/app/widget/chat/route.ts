import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 60;

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const body = (await request.json().catch(() => ({}))) as {
    message?: unknown;
    locale?: unknown;
  };
  const botId = searchParams.get("botId");
  const message = typeof body.message === "string" ? body.message : null;
  const sessionId = searchParams.get("session_id");
  const locale = typeof body.locale === "string" ? body.locale : null;

  if (!botId || message === null) {
    return NextResponse.json(
      { detail: "botId and message are required" },
      { status: 400 }
    );
  }

  const params = new URLSearchParams({ bot_id: botId });
  if (sessionId) params.set("session_id", sessionId);
  if (locale) params.set("locale", locale);

  const res = await fetch(`${API_URL}/widget/chat?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, locale }),
    cache: "no-store",
  });

  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  }

  return new Response(res.body, {
    status: res.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
