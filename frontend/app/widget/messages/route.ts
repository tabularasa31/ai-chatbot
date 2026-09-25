import { NextRequest } from "next/server";
import { proxyToApi } from "@/lib/widget-proxy";

// Cursor poll for the widget: everything written after `after_message_id`.
// Separate from /widget/history, which bootstraps the conversation on mount —
// this one is called every few seconds while a human is answering, so it stays
// as thin as the proxy can make it.
export async function GET(request: NextRequest) {
  return proxyToApi(request, "/widget/messages", {
    method: "GET",
    params: [
      { name: "bot_id", aliases: ["botId"], required: true },
      { name: "session_id", required: true },
      { name: "after_message_id" },
    ],
  });
}
