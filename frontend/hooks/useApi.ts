import useSWR from "swr";
import { api } from "@/lib/api";
import type {
  TenantMeResponse,
  BotResponse,
  SupportSettingsResponse,
  TenantPlanResponse,
  DisclosureConfigResponse,
  ChatSessionSummary,
  ChatSessionLogs,
  EscalationTicket,
  TenantMember,
} from "@/lib/api";

export function useClientMe() {
  return useSWR<TenantMeResponse>("client/me", () => api.clients.getMe());
}

export function useBots() {
  return useSWR<BotResponse[]>("bots", () => api.bots.list());
}

export function useMembers() {
  return useSWR<TenantMember[]>("tenant/members", () => api.members.list());
}

export function useSupportSettings() {
  return useSWR<SupportSettingsResponse>("support-settings", () => api.support.get());
}

export function usePlan() {
  return useSWR<TenantPlanResponse>("tenant-plan", () => api.plan.get());
}

export function useBotDisclosure(botId: string | null | undefined) {
  return useSWR<DisclosureConfigResponse>(
    botId ? `bot/${botId}/disclosure` : null,
    () => api.bots.getDisclosure(botId!)
  );
}

export function useChatSessions() {
  return useSWR<ChatSessionSummary[]>("chat/sessions", () => api.chat.listSessions());
}

export function useChatSessionLogs(sessionId: string | null) {
  return useSWR<ChatSessionLogs>(
    sessionId ? `chat/session/${sessionId}/logs` : null,
    () => api.chat.getSessionLogs(sessionId!)
  );
}

export function useEscalations(status?: string) {
  return useSWR<EscalationTicket[]>(
    ["escalations", status ?? ""],
    () => api.escalations.list(status ? { status } : undefined)
  );
}
