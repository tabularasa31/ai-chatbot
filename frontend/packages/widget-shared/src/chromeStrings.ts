// Widget chrome copy needed by both the loader (host page) and the widget-app
// (iframe). Offline-critical: these show even when the server is unreachable.
// English only until chrome localisation is decided.

export type SharedChromeKey =
  | "panel_name"
  | "open_chat"
  | "close_chat"
  | "load_failed"
  | "loading_overlay"
  | "retry";

const EN: Record<SharedChromeKey, string> = {
  panel_name: "Assistant",
  open_chat: "Open chat",
  close_chat: "Close chat",
  load_failed: "Chat didn't load.",
  loading_overlay: "Loading…",
  retry: "Try again",
};

export function sharedChromeString(key: SharedChromeKey): string {
  return EN[key];
}
