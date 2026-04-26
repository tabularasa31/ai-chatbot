import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export async function GET(request: NextRequest) {
  const auth = request.headers.get("Authorization");
  const token = request.cookies.get("chat9_token")?.value;
  const authorization = auth ?? (token ? `Bearer ${token}` : null);
  if (!authorization) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  const res = await fetch(`${API_URL}/auth/me/widget-token`, {
    headers: { Authorization: authorization },
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
