import { NextRequest } from "next/server";
import { proxyToApi } from "@/lib/widget-proxy";

export async function POST(request: NextRequest) {
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

  return proxyToApi(request, "/widget/escalate", {
    method: "POST",
    params: [
      { name: "bot_id", aliases: ["botId"], required: true },
      { name: "session_id", required: true },
    ],
    body: JSON.stringify({
      user_note: body.user_note ?? null,
      trigger: body.trigger ?? "user_request",
      failure_type: body.failure_type ?? null,
      original_user_message: body.original_user_message ?? null,
    }),
  });
}
