import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
const EDGE_PROXY_SECRET = process.env.EDGE_PROXY_SECRET || "";

// To the API every call through these routes comes from Vercel, so the
// visitor's address is passed on for its rate limits. Vercel overwrites
// x-real-ip with the caller's address; the secret makes the API trust the claim.
export function visitorHeaders(request: NextRequest): Record<string, string> {
  const ip = request.headers.get("x-real-ip");
  if (!EDGE_PROXY_SECRET || !ip) return {};
  return { "X-Chat9-Visitor-IP": ip, "X-Chat9-Edge-Secret": EDGE_PROXY_SECRET };
}

export type WidgetProxyParam = {
  name: string;
  /** Alternate query-string keys to read the value from, e.g. "botId". */
  aliases?: string[];
  required?: boolean;
};

// Proxies a widget request to `${API_URL}${path}`, forwarding the listed
// query params (with alias support) and passing the JSON response through
// status-for-status. Not for /widget/chat — that route streams SSE.
export async function proxyToApi(
  request: NextRequest,
  path: string,
  options: {
    method: "GET" | "POST";
    params?: WidgetProxyParam[];
    body?: BodyInit;
  },
): Promise<NextResponse> {
  const { searchParams } = new URL(request.url);
  const forwarded = new URLSearchParams();
  let hasMissing = false;

  for (const param of options.params ?? []) {
    const value =
      searchParams.get(param.name) ??
      param.aliases?.reduce<string | null>((found, alias) => found ?? searchParams.get(alias), null) ??
      null;
    if (value) {
      forwarded.set(param.name, value);
    } else if (param.required) {
      hasMissing = true;
    }
  }

  if (hasMissing) {
    const requiredNames = (options.params ?? []).filter((p) => p.required).map((p) => p.name);
    const detail =
      requiredNames.length > 1
        ? `${requiredNames.join(" and ")} are required`
        : `${requiredNames[0]} is required`;
    return NextResponse.json({ detail }, { status: 400 });
  }

  const qs = forwarded.toString();
  const res = await fetch(`${API_URL}${path}${qs ? `?${qs}` : ""}`, {
    method: options.method,
    headers: {
      ...visitorHeaders(request),
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body,
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
