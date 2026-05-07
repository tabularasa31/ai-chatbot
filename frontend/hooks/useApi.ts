import useSWR from "swr";
import { api } from "@/lib/api";
import type {
  TenantMeResponse,
  BotResponse,
  SupportSettingsResponse,
  DisclosureConfigResponse,
  ChatSessionSummary,
  ChatSessionLogs,
  EscalationTicket,
} from "@/lib/api";

export function useClientMe() {
  return useSWR<TenantMeResponse>("client/me", () => api.tenants.getMe());
}

export function useBots() {
  return useSWR<BotResponse[]>("bots", () => api.bots.list());
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
