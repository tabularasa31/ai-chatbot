// Visible widget chrome copy, English source. Every string a visitor can see
// outside the bot's own replies goes through here, so localising the chrome
// later touches this file only. Languages other than English are partial and
// fall back to English per key. The bot's replies are LLM-generated in the
// visitor's language and never come from this table.

import { sharedChromeString, type SharedChromeKey } from "@chat9/widget-shared";

type AppStringKey =
  | "unavailable"
  | "composer_label"
  | "composer_placeholder"
  | "send_message"
  | "status_working"
  | "status_writing"
  | "status_stalled"
  | "new_conversation"
  | "ticket_waiting"
  | "ticket_live"
  | "ticket_open"
  | "copy_code"
  | "code_copied"
  | "opens_new_tab"
  | "error_send"
  | "error_rate_limit"
  | "error_greeting"
  | "error_support"
  | "contact_support_button"
  | "support_notified"
  | "powered_by";

export type StringKey = AppStringKey | SharedChromeKey;

const EN: Record<AppStringKey, string> = {
  unavailable: "Chat is unavailable.",
  composer_label: "Message",
  composer_placeholder: "Type a message…",
  send_message: "Send message",
  status_working: "Looking it up",
  status_writing: "Writing answer",
  status_stalled: "Still working on it",
  new_conversation: "New conversation",
  ticket_waiting: "Ticket {n}. Waiting for a person to reply.",
  ticket_live: "Ticket {n}. A person is in this chat.",
  ticket_open: "Ticket {n}",
  copy_code: "Copy code",
  code_copied: "Code copied",
  opens_new_tab: "Opens in a new tab",
  error_send: "Couldn't send your message.",
  error_rate_limit: "Too many messages. Wait a moment.",
  error_greeting: "Couldn't start the chat.",
  error_support: "Couldn't reach support.",
  contact_support_button: "Contact support",
  support_notified: "Support has been notified. Someone from the team will follow up.",
  powered_by: "Powered by Chat9",
};

const LOCALES: Record<string, Partial<Record<AppStringKey, string>>> = {
  en: EN,
  ru: {
    contact_support_button: "Связаться с поддержкой",
    support_notified: "Поддержка уведомлена. С вами свяжутся.",
  },
};

const SHARED_KEYS: ReadonlySet<StringKey> = new Set<SharedChromeKey>([
  "panel_name",
  "open_chat",
  "close_chat",
  "resize_chat",
  "load_failed",
  "loading",
  "try_again_button",
]);

function isSharedKey(key: StringKey): key is SharedChromeKey {
  return SHARED_KEYS.has(key);
}

export function t(locale: string | null | undefined, key: StringKey, vars?: { n: string }): string {
  const text = isSharedKey(key) ? sharedChromeString(key) : textFor(locale, key);
  return vars ? text.replace("{n}", vars.n) : text;
}

function textFor(locale: string | null | undefined, key: AppStringKey): string {
  const primary = (locale ?? "en").toLowerCase().split(/[-_]/)[0];
  return LOCALES[primary]?.[key] ?? EN[key];
}
