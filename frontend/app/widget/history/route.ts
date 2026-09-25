import { NextRequest } from "next/server";
import { proxyToApi } from "@/lib/widget-proxy";

export async function GET(request: NextRequest) {
  return proxyToApi(request, "/widget/history", {
    method: "GET",
    params: [
      { name: "bot_id", aliases: ["botId"], required: true },
      { name: "session_id", required: true },
    ],
  });
}
