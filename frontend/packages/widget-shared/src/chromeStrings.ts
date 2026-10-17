// Widget chrome copy needed by both the loader (host page) and the widget-app
// (iframe). Offline-critical: these show even when the server is unreachable.
// Languages other than English are partial; a missing key falls back to English.

export type SharedChromeKey =
  | "panel_name"
  | "open_chat"
  | "close_chat"
  | "load_failed"
  | "loading"
  | "try_again_button";

type SharedChromeStrings = Record<SharedChromeKey, string>;

const EN: SharedChromeStrings = {
  panel_name: "Assistant",
  open_chat: "Open chat",
  close_chat: "Close chat",
  load_failed: "Chat didn't load.",
  loading: "Loading…",
  try_again_button: "Try again",
};

const SHARED_CHROME_LOCALES: Record<string, Partial<SharedChromeStrings>> = {
  en: EN,
  ru: { try_again_button: "Попробовать ещё раз", loading: "Загрузка…" },
};

export function sharedChromeString(locale: string | null | undefined, key: SharedChromeKey): string {
  const primary = (locale ?? "en").toLowerCase().split(/[-_]/)[0];
  return SHARED_CHROME_LOCALES[primary]?.[key] ?? EN[key];
}
