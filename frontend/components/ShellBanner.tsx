"use client";

import type { LlmAlertType } from "@/lib/api";
import { useClientMe, useLlmAlert } from "@/hooks/useApi";
import { PageBanner, type PageBannerAction } from "@/components/ui/page-banner";
import type { ShellState } from "@/lib/shell-state";

type BannerContent = {
  tone: "danger" | "warning";
  lead: string;
  text: string;
  action?: PageBannerAction;
};

const OPENAI_KEY_SETTINGS_HREF = "/settings#openai-key";

const LLM_ALERT_BANNERS: Partial<Record<LlmAlertType, BannerContent>> = {
  invalid_api_key: {
    tone: "danger",
    lead: "OpenAI API key invalid.",
    text: "OpenAI rejected your key, so the bot cannot answer until you update it.",
    action: { label: "Update key", href: OPENAI_KEY_SETTINGS_HREF },
  },
  quota_exhausted: {
    tone: "danger",
    lead: "OpenAI quota exhausted.",
    text: "Your key is out of credits, so the bot cannot answer until you add credits.",
    action: {
      label: "Top up balance",
      href: "https://platform.openai.com/settings/organization/billing",
      external: true,
    },
  },
};

const KEY_NOT_SET_BANNER: BannerContent = {
  tone: "danger",
  lead: "OpenAI API key not set.",
  text: "The bot cannot answer until you add your key.",
  action: { label: "Add key", href: OPENAI_KEY_SETTINGS_HREF },
};

const EMAIL_NOT_VERIFIED_BANNER: BannerContent = {
  tone: "warning",
  lead: "Your email is not verified.",
  text: "Check your inbox for the verification link.",
};

/** The one banner the page shows: blocking conditions first, then the email warning. */
export function useShellBanner(initialState: ShellState = {}): BannerContent | null {
  const { data: client } = useClientMe(initialState.client);
  const { data: llmAlert } = useLlmAlert(initialState.llmAlert);

  if (!client) return null;
  const blocking =
    (llmAlert?.type ? LLM_ALERT_BANNERS[llmAlert.type] : undefined) ??
    (client.has_openai_key ? undefined : KEY_NOT_SET_BANNER);
  const banner = blocking ?? (client.is_verified ? null : EMAIL_NOT_VERIFIED_BANNER);
  if (!banner) return null;
  if (client.role !== "owner") return { ...banner, action: undefined };
  return banner;
}

export function ShellBanner({ initialState }: { initialState: ShellState }) {
  const banner = useShellBanner(initialState);
  return banner ? <PageBanner {...banner} /> : null;
}
