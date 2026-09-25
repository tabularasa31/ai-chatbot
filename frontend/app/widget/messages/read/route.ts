import { NextRequest } from "next/server";
import { proxyToApi } from "@/lib/widget-proxy";

// Read receipt for the widget: the visitor has had `message_id` on screen.
// Companion to the cursor poll next door; it is what decides whether an
// operator's reply gets mailed to the visitor after the grace period.
export async function POST(request: NextRequest) {
  const body = await request.text();
  return proxyToApi(request, "/widget/messages/read", {
    method: "POST",
    params: [
      { name: "bot_id", aliases: ["botId"], required: true },
      { name: "session_id", required: true },
    ],
    body,
  });
}
