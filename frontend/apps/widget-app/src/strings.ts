// Pre-mount UI copy + LLM-unavailable button labels. Kept minimal — once
// ChatWidget mounts, regular bot copy is LLM-generated in the user's language
// (project rule: "User-facing strings always localized"). The exceptions:
// pre-mount diagnostics and the LLM-unavailable degraded path, where the LLM
// is by definition unreachable. New languages: add an entry to LOCALES with
// all keys filled.

type StringKey =
  | "loading"
  | "misconfigured_title"
  | "misconfigured_body"
  | "try_again_button"
  | "contact_support_button"
  | "support_notified";

const LOCALES: Record<string, Record<StringKey, string>> = {
  en: {
    loading: "Loading…",
    misconfigured_title: "Widget misconfigured",
    misconfigured_body: "Missing required loader parameters: botId and apiBase.",
    try_again_button: "Try again",
    contact_support_button: "Contact support",
    support_notified: "Support has been notified. Someone from the team will follow up.",
  },
  ru: {
    loading: "Загрузка…",
    misconfigured_title: "Виджет настроен неверно",
    misconfigured_body: "Отсутствуют обязательные параметры loader'а: botId и apiBase.",
    try_again_button: "Попробовать ещё раз",
    contact_support_button: "Связаться с поддержкой",
    support_notified: "Поддержка уведомлена. С вами свяжутся.",
  },
};

export function pickLanguage(locale: string | null | undefined): string {
  if (!locale) return "en";
  const primary = locale.toLowerCase().split(/[-_]/)[0];
  return primary in LOCALES ? primary : "en";
}

export function t(locale: string | null | undefined, key: StringKey): string {
  return LOCALES[pickLanguage(locale)][key];
}
