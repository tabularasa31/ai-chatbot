import useSWR from "swr";
import { api } from "@/lib/api";
import type {
  TenantMeResponse,
  BotResponse,
  SupportSettingsResponse,
  DisclosureConfigResponse,
  TenantMemberList,
  InboxList,
  InboxSummary,
  Thread,
  AnalyticsPeriod,
  AnalyticsSummaryResponse,
  AuthUser,
} from "@/lib/api";

export function useClientMe() {
  return useSWR<TenantMeResponse>("client/me", () => api.clients.getMe());
}

export function useAuthUser(enabled = true) {
  return useSWR<AuthUser>(enabled ? "auth/me" : null, () => api.auth.getMe());
}

export function useBots() {
  return useSWR<BotResponse[]>("bots", () => api.bots.list());
}

export function useActiveBot({ fallbackToFirst = false }: { fallbackToFirst?: boolean } = {}) {
  const { data: bots, isLoading, isValidating, error, mutate } = useBots();
  const activeBot =
    bots?.find((b) => b.is_active) ?? (fallbackToFirst ? bots?.[0] : undefined) ?? null;
  return { bots, activeBot, isLoading, isValidating, error, mutate };
}

export function useMembers() {
  return useSWR<TenantMemberList>("tenant/members", () => api.members.list());
}

export function useSupportSettings() {
  return useSWR<SupportSettingsResponse>("support-settings", () => api.support.get());
}

export function useBotDisclosure(botId: string | null | undefined) {
  return useSWR<DisclosureConfigResponse>(
    botId ? `bot/${botId}/disclosure` : null,
    () => api.bots.getDisclosure(botId!)
  );
}

export function useInbox(scope: "attention" | "all", refreshInterval = 0) {
  return useSWR<InboxList>(["operator/inbox", scope], () => api.operator.inbox(scope), {
    refreshInterval,
  });
}

export function useInboxSummary(refreshInterval = 0) {
  return useSWR<InboxSummary>("operator/inbox/summary", () => api.operator.summary(), {
    refreshInterval,
  });
}

export function useAnalyticsSummary(period: AnalyticsPeriod) {
  return useSWR<AnalyticsSummaryResponse>(["analytics/summary", period], () => api.analytics.summary(period), {
    keepPreviousData: true,
  });
}

export function useThread(sessionId: string | null, refreshInterval = 0) {
  return useSWR<Thread>(
    sessionId ? `operator/sessions/${sessionId}` : null,
    () => api.operator.thread(sessionId!),
    { refreshInterval }
  );
}
