import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const botId = searchParams.get("bot_id") ?? searchParams.get("botId");
  const locale = searchParams.get("locale");

  if (!botId) {
    return NextResponse.json(
      { detail: "bot_id is required" },
      { status: 400 },
    );
  }

  const params = new URLSearchParams({ bot_id: botId });
  if (locale) params.set("locale", locale);

  const res = await fetch(`${API_URL}/widget/config?${params}`);
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
