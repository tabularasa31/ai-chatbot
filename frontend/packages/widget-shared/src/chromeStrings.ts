// Widget chrome copy needed by both the loader (host page) and the widget-app
// (iframe). Offline-critical: these show even when the server is unreachable.
// English only until chrome localisation is decided.

export type SharedChromeKey =
  | "panel_name"
  | "open_chat"
  | "close_chat"
  | "load_failed"
  | "loading"
  | "try_again_button";

const EN: Record<SharedChromeKey, string> = {
  panel_name: "Assistant",
  open_chat: "Open chat",
  close_chat: "Close chat",
  load_failed: "Chat didn't load.",
  loading: "Loading…",
  try_again_button: "Try again",
};

export function sharedChromeString(key: SharedChromeKey): string {
  return EN[key];
}
