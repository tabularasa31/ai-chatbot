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
} from "@/lib/api";

export function useClientMe() {
  return useSWR<TenantMeResponse>("client/me", () => api.clients.getMe());
}

export function useBots() {
  return useSWR<BotResponse[]>("bots", () => api.bots.list());
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

export function useThread(sessionId: string | null, refreshInterval = 0) {
  return useSWR<Thread>(
    sessionId ? `operator/sessions/${sessionId}` : null,
    () => api.operator.thread(sessionId!),
    { refreshInterval }
  );
}
