// Chat9 widget loader — explicit lifecycle API.
//
// Public surface:
//   <script src="https://widget.getchat9.live/widget.js" data-bot-id="ch_..."></script>
//   <script>
//     Chat9Widget.start({
//       userHints: { name: "Anna", email: "anna@example.com", locale: "ru-RU" },
//       mode: "bubble",        // "bubble" | "inline"
//       color: "#a855f7",
//       position: "right",      // "right" | "left"
//       target: "<elementId>", // for mode: "inline"
//       topClearance: 56,      // px reserved at viewport top (e.g. fixed navbar height)
//       apiBase: "https://...",
//       widgetBase: "https://..."
//     });
//   </script>
//
// `data-bot-id` is the only data-attribute on the loader script (it identifies
// the bot before any config is read). Loading the script does NOT mount any UI
// on its own — call `Chat9Widget.start(config)` to mount.
//
// Lifecycle:
//   Chat9Widget.start(config?)   — mount FAB + iframe. No-op if already started.
//   Chat9Widget.stop()           — unmount DOM/listeners. Script and Chat9Widget
//                                  stay alive; ready for another start().
//   Chat9Widget.setHints(hints)  — update identity in the running iframe.
//                                  If stopped, remembered for the next start().
//                                  Pass null to clear (anonymous).
//   Chat9Widget.isStarted()      — true while the widget is mounted.
//   Chat9Widget.destroy()        — TERMINAL teardown: stop() + delete
//                                  window.Chat9Widget. To use the widget
//                                  again on this page you must reload
//                                  widget.js (the API binding is gone).
//                                  Most consumers want stop() instead.
//
// Russian edge proxy: load this script from widget-ru.getchat9.live instead
// of widget.getchat9.live, and the loader infers the matching API origin
// (api-ru.getchat9.live) from its own scriptOrigin. No flag needed.

// Marks this file as a module so `declare global` is allowed under TS's
// stricter rules; the IIFE wrapper still produces a side-effect-only bundle.
export {};

type UserHints = {
  user_id?: string;
  email?: string;
  name?: string;
  locale?: string;
  plan_tier?: string;
  audience_tag?: string;
};

type StartConfig = {
  userHints?: UserHints;
  mode?: "bubble" | "inline";
  color?: string;
  position?: "right" | "left";
  target?: string;
  topClearance?: number;
  apiBase?: string;
  widgetBase?: string;
};

type Chat9WidgetApi = {
  start: (config?: StartConfig) => void;
  stop: () => void;
  setHints: (hints: UserHints | null) => void;
  isStarted: () => boolean;
  destroy: () => void;
};

declare global {
  interface Window {
    Chat9Widget?: Chat9WidgetApi;
  }
}

(function () {
  // Default API origin (the dashboard, where /widget/* and /api/widget-* proxy
  // to the backend). Tenants on staging override via StartConfig.apiBase.
  const DEFAULT_API_BASE = "https://getchat9.live";

  // RU edge proxy API origin. Selected automatically when the loader script
  // is served from widget-ru.getchat9.live (the matching widget edge).
  // Bypasses TSPU throttling on Vercel/Railway from Russian ISPs.
  const RU_API_BASE = "https://api-ru.getchat9.live";

  const currentScript: HTMLScriptElement | null =
    (document.currentScript as HTMLScriptElement | null) ??
    (() => {
      const scripts = document.getElementsByTagName("script");
      return (scripts[scripts.length - 1] as HTMLScriptElement | null) ?? null;
    })();

  if (!currentScript) {
    console.error("Chat9: cannot locate <script> element. Loader must run synchronously from a script tag.");
    return;
  }

  const botId = (currentScript.dataset.botId ?? "").trim();
  if (!botId) {
    console.error("Chat9: data-bot-id is required on the loader script tag.");
    return;
  }

  // Widget UI base default: derived from the script's own origin. Strip trailing
  // slashes so we can append "/v1/?…" predictably.
  const scriptOrigin = (() => {
    try {
      return new URL(currentScript.src).origin;
    } catch {
      return "";
    }
  })();

  function resolveWidgetBase(rawWidgetBase: string | undefined): string {
    const base = (rawWidgetBase || `${scriptOrigin}/v1/`).replace(/\/+$/, "/");
    return base.endsWith("/") ? base : base + "/";
  }

  function resolveWidgetOrigin(widgetBaseUrl: string): string {
    try {
      return new URL(widgetBaseUrl).origin;
    } catch {
      return "";
    }
  }

  function resolveApiBase(rawApiBase: string | undefined, widgetOrigin: string): string {
    const apiBaseDefault =
      widgetOrigin === "https://widget-ru.getchat9.live" ? RU_API_BASE : DEFAULT_API_BASE;
    return (rawApiBase || apiBaseDefault).replace(/\/+$/, "");
  }

  // Drop unknown keys, empty/non-string values. Length capping happens server-side.
  function sanitizeHints(raw: UserHints | undefined | null): UserHints | null {
    if (!raw || typeof raw !== "object") return null;
    const allowed: (keyof UserHints)[] = [
      "user_id",
      "email",
      "name",
      "locale",
      "plan_tier",
      "audience_tag",
    ];
    const out: UserHints = {};
    for (const key of allowed) {
      const value = raw[key];
      if (typeof value === "string" && value.trim()) {
        out[key] = value.trim();
      }
    }
    return Object.keys(out).length > 0 ? out : null;
  }

  // Convert #RGB or #RRGGBB → rgba; returns null for named colors / rgb() etc.
  function hexToRgba(hex: string | null, a: number): string | null {
    const m = hex && hex.match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
    if (!m) return null;
    const full = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1];
    return `rgba(${parseInt(full.slice(0, 2), 16)},${parseInt(full.slice(2, 4), 16)},${parseInt(full.slice(4, 6), 16)},${a})`;
  }

  // ── Persistent loader state (lives across start/stop cycles) ──────────────
  // currentHints mirrors what a mounted iframe should see. setHints updates it
  // and, if mounted, posts immediately; otherwise it waits for the next start.
  let currentHints: UserHints | null = null;

  // Per-mount handles. null when stopped.
  type MountHandles = {
    container: HTMLDivElement;
    resizeOverlay: HTMLDivElement;
    iframe: HTMLIFrameElement;
    widgetOrigin: string;
    onMessage: (e: MessageEvent) => void;
    onMouseMove: (e: MouseEvent) => void;
    onMouseUp: () => void;
  };
  let handles: MountHandles | null = null;

  function postHintsToIframe(h: MountHandles, hints: UserHints | null) {
    if (!h.widgetOrigin) return;
    const message = hints
      ? { type: "chat9:hints", userHints: hints }
      : { type: "chat9:no-hints" };
    h.iframe.contentWindow?.postMessage(message, h.widgetOrigin);
  }

  function start(config: StartConfig = {}) {
    if (handles) {
      console.warn("Chat9: already started — call stop() before start() to reconfigure.");
      return;
    }

    const mode = (config.mode || "bubble").toLowerCase();
    const color = config.color || null;
    const position = (config.position || "right").toLowerCase();
    const targetId = config.target || null;
    const topClearance =
      typeof config.topClearance === "number" && config.topClearance > 0
        ? Math.round(config.topClearance)
        : 0;

    const widgetBaseUrl = resolveWidgetBase(config.widgetBase);
    const widgetOrigin = resolveWidgetOrigin(widgetBaseUrl);
    const apiBase = resolveApiBase(config.apiBase, widgetOrigin);

    const incomingHints = sanitizeHints(config.userHints);
    if (incomingHints) currentHints = incomingHints;

    const browserLocale =
      currentHints?.locale ||
      (typeof navigator !== "undefined" &&
        (navigator.language || (navigator as Navigator & { userLanguage?: string }).userLanguage)) ||
      null;

    function buildIframeSrc(): string {
      const params = new URLSearchParams();
      params.set("botId", botId);
      if (browserLocale) params.set("locale", browserLocale);
      if (apiBase) params.set("apiBase", apiBase);
      if (typeof window !== "undefined" && window.location?.origin) {
        params.set("parentOrigin", window.location.origin);
      }
      return widgetBaseUrl + "?" + params.toString();
    }

    function makeIframe(): HTMLIFrameElement {
      const f = document.createElement("iframe");
      f.src = buildIframeSrc();
      f.id = "chat9-widget-iframe";
      f.style.cssText = "width:100%;height:100%;border:none;display:block;";
      f.allow = "microphone; camera";
      return f;
    }

    // Persistent listener: every chat9:ready from the iframe gets answered with
    // the latest currentHints. The widget-app may post chat9:ready more than
    // once (e.g. on identity-driven remount), so we stay subscribed for the
    // entire mount lifetime.
    function onMessage(event: MessageEvent) {
      if (!handles) return;
      if (event.source !== handles.iframe.contentWindow) return;
      const payload = event.data as { type?: string } | null;
      if (!payload || typeof payload !== "object" || payload.type !== "chat9:ready") return;
      if (!handles.widgetOrigin) {
        console.error("Chat9: cannot determine widget origin — set widgetBase. Aborting handshake.");
        return;
      }
      postHintsToIframe(handles, currentHints);
    }

    // ── INLINE MODE ──────────────────────────────────────────────────────────
    if (mode === "inline") {
      const targetEl = targetId ? document.getElementById(targetId) : null;
      if (!targetEl) {
        console.error(
          "Chat9 inline: target element not found. Set config.target = \"<elementId>\" and ensure the element exists."
        );
        return;
      }

      const inlineFrame = makeIframe();
      inlineFrame.style.cssText =
        "width:100%;height:600px;border:none;display:block;border-radius:12px;overflow:hidden;";

      // Inline mode reuses the container slot from targetEl. We wrap in our own
      // synthetic container so stop() can do a clean .remove() without nuking
      // sibling content the host page may have placed in targetEl.
      const inlineContainer = document.createElement("div");
      inlineContainer.id = "chat9-widget-container";
      inlineContainer.style.cssText = "width:100%;";
      inlineContainer.appendChild(inlineFrame);

      // Synthetic resizeOverlay for type-uniformity with bubble mode (unused).
      const resizeOverlay = document.createElement("div");

      targetEl.innerHTML = "";
      targetEl.appendChild(inlineContainer);

      handles = {
        container: inlineContainer,
        resizeOverlay,
        iframe: inlineFrame,
        widgetOrigin,
        onMessage,
        onMouseMove: () => {},
        onMouseUp: () => {},
      };
      window.addEventListener("message", handles.onMessage);
      return;
    }

    // ── BUBBLE MODE ──────────────────────────────────────────────────────────
    let isOpen = false;
    let isResizing = false;
    let resizeStartX = 0;
    let resizeStartY = 0;
    let resizeStartW = 0;
    let resizeStartH = 0;

    const fabBg = color || "linear-gradient(135deg,#e879f9,#a855f7)";
    const colorRgba1 = hexToRgba(color, 0.4);
    const colorRgba2 = hexToRgba(color, 0.55);
    const fabShadow = colorRgba1
      ? `0 4px 18px ${colorRgba1}`
      : color
      ? "0 4px 18px rgba(0,0,0,0.28)"
      : "0 4px 18px rgba(168,85,247,0.45)";
    const fabShadowH = colorRgba2
      ? `0 6px 24px ${colorRgba2}`
      : color
      ? "0 6px 24px rgba(0,0,0,0.38)"
      : "0 6px 24px rgba(168,85,247,0.55)";

    const isLeft = position === "left";
    const hEdge = isLeft ? "left:20px;" : "right:20px;";
    const tOrigin = isLeft ? "bottom left" : "bottom right";
    const windowAlign = isLeft ? "align-items:flex-start;" : "align-items:flex-end;";

    const CHAT_ICON =
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>";

    const CLOSE_ICON =
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<line x1="18" y1="6" x2="6" y2="18" stroke="white" stroke-width="2.5" stroke-linecap="round"/>' +
      '<line x1="6" y1="6" x2="18" y2="18" stroke="white" stroke-width="2.5" stroke-linecap="round"/>' +
      "</svg>";

    const container = document.createElement("div");
    container.id = "chat9-widget-container";
    container.style.cssText =
      "position:fixed;bottom:20px;" +
      hEdge +
      "z-index:9999;display:flex;flex-direction:column;" +
      windowAlign +
      "gap:12px;";
    document.body.appendChild(container);

    const chatWindow = document.createElement("div");
    chatWindow.id = "chat9-chat-window";
    chatWindow.style.cssText =
      "display:none;width:400px;height:600px;min-width:280px;min-height:360px;" +
      "max-width:min(700px, calc(100vw - 40px));" +
      `max-height:min(820px, calc(100vh - 100px - ${topClearance}px));` +
      "position:relative;border-radius:16px;overflow:hidden;" +
      "box-shadow:0 8px 40px rgba(0,0,0,0.20);" +
      "opacity:0;transform:scale(0.93) translateY(10px);" +
      `transform-origin:${tOrigin};` +
      "transition:opacity 0.22s ease, transform 0.22s ease;";

    const resizeHandle = document.createElement("div");
    resizeHandle.id = "chat9-resize-handle";
    const handleCorner = isLeft ? "top:0;right:0;" : "top:0;left:0;";
    const handleCursor = isLeft ? "ne-resize" : "nw-resize";
    const handleRadius = isLeft ? "border-radius:0 0 0 6px;" : "border-radius:0 0 6px 0;";
    resizeHandle.style.cssText =
      "position:absolute;" +
      handleCorner +
      "width:28px;height:28px;cursor:" +
      handleCursor +
      ";z-index:20;display:flex;align-items:center;justify-content:center;" +
      handleRadius;
    resizeHandle.innerHTML =
      '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<circle cx="2" cy="10" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
      '<circle cx="6" cy="10" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
      '<circle cx="2" cy="6" r="1.2" fill="rgba(255,255,255,0.45)"/>' +
      "</svg>";

    const iframe = makeIframe();

    const resizeOverlay = document.createElement("div");
    resizeOverlay.style.cssText =
      "display:none;position:fixed;top:0;left:0;right:0;bottom:0;z-index:10000;cursor:" +
      handleCursor +
      ";";

    const fab = document.createElement("button");
    fab.id = "chat9-fab";
    fab.title = "Chat9";
    fab.style.cssText =
      "width:56px;height:56px;border-radius:50%;border:none;background:" +
      fabBg +
      ";cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:" +
      fabShadow +
      ";transition:transform 0.18s ease, box-shadow 0.18s ease;flex-shrink:0;outline:none;";
    fab.innerHTML = CHAT_ICON;

    fab.addEventListener("mouseenter", () => {
      fab.style.transform = "scale(1.08)";
      fab.style.boxShadow = fabShadowH;
    });
    fab.addEventListener("mouseleave", () => {
      fab.style.transform = "scale(1)";
      fab.style.boxShadow = fabShadow;
    });

    function openChat() {
      isOpen = true;
      chatWindow.style.display = "block";
      requestAnimationFrame(() => {
        chatWindow.style.opacity = "1";
        chatWindow.style.transform = "scale(1) translateY(0)";
      });
      fab.innerHTML = CLOSE_ICON;
    }

    function closeChat() {
      isOpen = false;
      chatWindow.style.opacity = "0";
      chatWindow.style.transform = "scale(0.93) translateY(10px)";
      setTimeout(() => {
        chatWindow.style.display = "none";
      }, 220);
      fab.innerHTML = CHAT_ICON;
    }

    fab.addEventListener("click", () => {
      if (isOpen) closeChat();
      else openChat();
    });

    resizeHandle.addEventListener("mousedown", (e) => {
      isResizing = true;
      resizeStartX = e.clientX;
      resizeStartY = e.clientY;
      resizeStartW = chatWindow.offsetWidth;
      resizeStartH = chatWindow.offsetHeight;
      resizeOverlay.style.display = "block";
      e.preventDefault();
    });

    function onMouseMove(e: MouseEvent) {
      if (!isResizing) return;
      const dx = isLeft ? e.clientX - resizeStartX : resizeStartX - e.clientX;
      const dy = resizeStartY - e.clientY;
      const newW = Math.max(280, Math.min(700, resizeStartW + dx));
      const newH = Math.max(360, Math.min(820, resizeStartH + dy));
      chatWindow.style.width = newW + "px";
      chatWindow.style.height = newH + "px";
      e.preventDefault();
    }

    function onMouseUp() {
      if (!isResizing) return;
      isResizing = false;
      resizeOverlay.style.display = "none";
    }

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);

    chatWindow.appendChild(resizeHandle);
    chatWindow.appendChild(iframe);
    container.appendChild(chatWindow);
    container.appendChild(fab);
    document.body.appendChild(resizeOverlay);

    handles = {
      container,
      resizeOverlay,
      iframe,
      widgetOrigin,
      onMessage,
      onMouseMove,
      onMouseUp,
    };
    window.addEventListener("message", handles.onMessage);
  }

  function stop() {
    if (!handles) return;
    window.removeEventListener("message", handles.onMessage);
    document.removeEventListener("mousemove", handles.onMouseMove);
    document.removeEventListener("mouseup", handles.onMouseUp);
    handles.container.remove();
    handles.resizeOverlay.remove();
    handles = null;
  }

  function setHints(hints: UserHints | null) {
    currentHints = sanitizeHints(hints);
    if (handles) postHintsToIframe(handles, currentHints);
  }

  function isStarted(): boolean {
    return handles !== null;
  }

  function destroy() {
    stop();
    delete window.Chat9Widget;
  }

  window.Chat9Widget = { start, stop, setHints, isStarted, destroy };
})();
