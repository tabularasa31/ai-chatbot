import { cookies } from "next/headers";
import type { TenantLlmAlertResponse, TenantMeResponse } from "@/lib/api";
import { AUTH_COOKIE_NAME } from "@/lib/session";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
const PREFETCH_TIMEOUT_MS = 1500;

export type ShellState = {
  client?: TenantMeResponse;
  llmAlert?: TenantLlmAlertResponse;
};

async function fetchAsUser<T>(path: string, token: string): Promise<T | undefined> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(PREFETCH_TIMEOUT_MS),
    });
    return res.ok ? ((await res.json()) as T) : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Server-side read of what the shell banner depends on, so the banner is in
 * the first paint instead of pushing the page down after hydration. Anything
 * missing here is fetched on the client as before.
 */
export async function prefetchShellState(): Promise<ShellState> {
  const token = cookies().get(AUTH_COOKIE_NAME)?.value;
  if (!token || !API_URL.startsWith("http")) return {};
  const [client, llmAlert] = await Promise.all([
    fetchAsUser<TenantMeResponse>("/tenants/me", token),
    fetchAsUser<TenantLlmAlertResponse>("/tenants/me/llm-alert", token),
  ]);
  return { client, llmAlert };
}
