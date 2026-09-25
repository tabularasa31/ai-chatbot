import { NextRequest } from "next/server";
import { proxyToApi } from "@/lib/widget-proxy";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  return proxyToApi(request, "/widget/session/init", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
