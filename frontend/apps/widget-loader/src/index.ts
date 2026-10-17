// Chat9 widget loader — explicit lifecycle API.
//
// Public surface:
//   <script src="https://widget.getchat9.live/widget.js" data-bot-id="ch_..."></script>
//   <script>
//     Chat9Widget.start({
//       userHints: { name: "Anna", email: "anna@example.com", locale: "ru-RU" },
//       mode: "bubble",        // "bubble" | "inline"
//       theme: "light",        // "light" | "dark"
//       color: "#7C3AED",      // launcher, send button and visitor bubble fill
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

import { sharedChromeString, type SharedChromeKey } from "../../../packages/widget-shared/src/chromeStrings";
import { ACCENT_ON_LIGHT, accentOnRgb, rgbToHex, type Rgb } from "../../../packages/widget-shared/src/accent";

type UserHints = {
  user_id?: string;
  user_hash?: string;
  email?: string;
  name?: string;
  locale?: string;
  plan_tier?: string;
  audience_tag?: string;
};

type StartConfig = {
  userHints?: UserHints;
  mode?: "bubble" | "inline";
  theme?: "light" | "dark";
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
  // Default API origin (the dashboard, where /widget/* proxies
  // to the backend). Tenants on staging override via StartConfig.apiBase.
  const DEFAULT_API_BASE = "https://getchat9.live";

  // RU edge proxy API origin. Selected automatically when the loader script
  // is served from widget-ru.getchat9.live (the matching widget edge).
  // Bypasses TSPU throttling on Vercel/Railway from Russian ISPs.
  const RU_API_BASE = "https://api-ru.getchat9.live";

  const PHONE_MEDIA_QUERY = "(max-width: 479px)";
  const IFRAME_READY_TIMEOUT_MS = 8000;

  // The host page has no access to our CSS variables, so the launcher and the
  // panel frame carry the design tokens as constants (DESIGN.md section 4).
  const LAUNCHER_DEFAULT_FILL = "#7C3AED";
  const LAUNCHER_HOVER_FILL = "#6D28D9";
  const LAUNCHER_ACTIVE_FILL = "#5B21B6";
  const LAUNCHER_SHADOW = "0 4px 12px rgba(15,23,42,0.12)";
  const LAUNCHER_FOCUS_RING = "0 0 0 2px #FFFFFF, 0 0 0 4px #1E293B";
  const CHROME_FG = "#FAF5FF";
  const CHROME_FG_MUTED = "#A9A3BA";
  const CHROME_ACCENT = "#A78BFA";
  const DEFAULT_PANEL_WIDTH = 380;
  const DEFAULT_PANEL_HEIGHT = 600;
  const MIN_PANEL_WIDTH = 280;
  const MIN_PANEL_HEIGHT = 360;
  const MAX_PANEL_WIDTH = 700;
  const MAX_PANEL_HEIGHT = 820;
  const PANEL_OFFSET = 20;
  const RESIZE_STEP = 20;
  const RESIZE_STEP_LARGE = 80;
  const GRIP_SIZE = 24;
  const GRIP_FADE_MS = 120;
  const PANEL_RADIUS = "16px";
  const COMPUTED_RGB = /^rgba?\((\d{1,3}(?:\.\d+)?)[\s,]+(\d{1,3}(?:\.\d+)?)[\s,]+(\d{1,3}(?:\.\d+)?)(?:[\s,/]+[\d.]+%?)?\)$/;
  const GRIP_MEDIA_QUERY = "(pointer: fine) and (hover: hover)";

  type PanelPalette = {
    surface: string;
    line: string;
    lineStrong: string;
    fg: string;
    shadow: string;
  };
  const PANEL_PALETTES: Record<"light" | "dark", PanelPalette> = {
    light: {
      surface: "#FFFFFF",
      line: "#E2E8F0",
      lineStrong: "#7C8BA1",
      fg: "#1E293B",
      shadow: "0 16px 40px rgba(15,23,42,0.16)",
    },
    dark: {
      surface: "#222233",
      line: "#3A3A50",
      lineStrong: "#7C7793",
      fg: "#FAF5FF",
      shadow: "0 16px 40px rgba(0,0,0,0.5)",
    },
  };

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
      "user_hash",
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
        out[key] = key === "user_hash" ? value.trim().slice(0, 128) : value.trim();
      }
    }
    return Object.keys(out).length > 0 ? out : null;
  }

  // Any CSS colour the tenant passes (hex, rgb(), named) resolves through the
  // browser to concrete channels; null when the browser cannot parse it.
  function resolveColor(cssColor: string): Rgb | null {
    const probe = document.createElement("span");
    probe.style.color = cssColor;
    if (!probe.style.color) return null;
    document.body.appendChild(probe);
    const computed = getComputedStyle(probe).color;
    probe.remove();
    const match = COMPUTED_RGB.exec(computed);
    if (!match) return null;
    const channels = [Number(match[1]), Number(match[2]), Number(match[3])];
    return channels.every((c) => c <= 255) ? [channels[0], channels[1], channels[2]] : null;
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
    teardown: () => void;
  };
  let handles: MountHandles | null = null;

  function postHintsToIframe(h: MountHandles, hints: UserHints | null) {
    if (!h.widgetOrigin) return;
    const message = hints
      ? { type: "chat9:hints", userHints: hints }
      : { type: "chat9:no-hints" };
    h.iframe.contentWindow?.postMessage(message, h.widgetOrigin);
  }

  // Whether the visitor can see the chat panel. The widget-app cannot tell on
  // its own: a collapsed bubble hides the iframe with display:none, and the
  // iframe's own document.hidden still says "visible". It reads this to decide
  // when a message has actually been on screen. It also carries whether the
  // panel is full-screen, which decides where focus goes on open.
  function postPanelStateToIframe(h: MountHandles, open: boolean, fullScreen: boolean, grip: boolean) {
    if (!h.widgetOrigin) return;
    h.iframe.contentWindow?.postMessage({ type: "chat9:panel", open, fullScreen, grip }, h.widgetOrigin);
  }

  function start(config: StartConfig = {}) {
    if (handles) {
      console.warn("Chat9: already started — call stop() before start() to reconfigure.");
      return;
    }

    const mode = (config.mode || "bubble").toLowerCase();
    const requestedTheme = (config.theme ?? "light").toString().toLowerCase();
    if (requestedTheme !== "light" && requestedTheme !== "dark") {
      console.warn(`Chat9: unknown theme "${config.theme}", using "light".`);
    }
    const theme: "light" | "dark" = requestedTheme === "dark" ? "dark" : "light";
    const color = config.color || null;
    const accentRgb = color ? resolveColor(color) : null;
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

    function chromeString(key: SharedChromeKey): string {
      return sharedChromeString(key);
    }

    function buildIframeSrc(): string {
      const params = new URLSearchParams();
      params.set("botId", botId);
      params.set("theme", theme);
      params.set("mode", mode === "inline" ? "inline" : "bubble");
      if (accentRgb) params.set("accent", rgbToHex(accentRgb));
      if (position === "left") params.set("position", "left");
      if (browserLocale) params.set("locale", browserLocale);
      if (apiBase) params.set("apiBase", apiBase);
      if (typeof window !== "undefined" && window.location?.origin) {
        params.set("parentOrigin", window.location.origin);
      }
      return widgetBaseUrl + "?" + params.toString();
    }

    // Bubble mode leaves ``src`` unset until the first open: the widget app
    // bootstraps a session and a greeting as soon as it loads, and doing that
    // for every page view minted an empty conversation per visitor who never
    // clicked the bubble.
    function makeIframe(load: boolean): HTMLIFrameElement {
      const f = document.createElement("iframe");
      if (load) f.src = buildIframeSrc();
      f.id = "chat9-widget-iframe";
      f.style.cssText = "width:100%;height:100%;border:none;display:block;";
      f.allow = "microphone; camera";
      return f;
    }

    // Persistent listener: every chat9:ready from the iframe gets answered with
    // the latest currentHints. The widget-app may post chat9:ready more than
    // once (e.g. on identity-driven remount), so we stay subscribed for the
    // entire mount lifetime.
    let panelOpen = mode === "inline";
    let gripShown = false;
    const phoneFullScreen = (): boolean => window.matchMedia(PHONE_MEDIA_QUERY).matches;
    let closePanel: (() => void) | null = null;
    let onIframeReady: (() => void) | null = null;
    function onMessage(event: MessageEvent) {
      if (!handles) return;
      if (event.source !== handles.iframe.contentWindow) return;
      const payload = event.data as { type?: string } | null;
      if (!payload || typeof payload !== "object") return;
      if (payload.type === "chat9:close") {
        if (event.origin === handles.widgetOrigin) closePanel?.();
        return;
      }
      if (payload.type !== "chat9:ready") return;
      onIframeReady?.();
      if (!handles.widgetOrigin) {
        console.error("Chat9: cannot determine widget origin — set widgetBase. Aborting handshake.");
        return;
      }
      postPanelStateToIframe(handles, panelOpen, mode === "bubble" && panelOpen && phoneFullScreen(), gripShown);
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

      // The target's content is replaced by our own wrapper so stop() can remove
      // the widget with a single .remove().
      const inlineContainer = document.createElement("div");
      inlineContainer.id = "chat9-widget-container";
      inlineContainer.style.cssText = "width:100%;height:100%;";
      targetEl.innerHTML = "";
      targetEl.appendChild(inlineContainer);

      // An empty wrapper resolves to a non-zero height only when the host gives the target
      // a definite one (fixed height, flex or grid stretch); otherwise fall back to the default.
      const hostSized = inlineContainer.offsetHeight > 0;
      if (!hostSized) inlineContainer.style.height = "auto";

      const frameHeight = hostSized ? "100%" : `${DEFAULT_PANEL_HEIGHT}px`;
      const inlineFrame = makeIframe(true);
      inlineFrame.style.cssText = `width:100%;height:${frameHeight};border:none;display:block;border-radius:12px;overflow:hidden;`;
      inlineContainer.appendChild(inlineFrame);

      // Synthetic resizeOverlay for type-uniformity with bubble mode (unused).
      const resizeOverlay = document.createElement("div");

      handles = {
        container: inlineContainer,
        resizeOverlay,
        iframe: inlineFrame,
        widgetOrigin,
        onMessage,
        teardown: () => {},
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
    let savedBodyOverflow = "";
    let bodyScrollLocked = false;
    let loadFailTimer: number | undefined;

    const palette = PANEL_PALETTES[theme];
    const launcherFill = accentRgb ? rgbToHex(accentRgb) : LAUNCHER_DEFAULT_FILL;
    const launcherHoverFilter = accentRgb ? "brightness(0.92)" : "";
    const launcherActiveFilter = accentRgb ? "brightness(0.85)" : "";
    const launcherGlyph = accentRgb ? rgbToHex(accentOnRgb(accentRgb)) : ACCENT_ON_LIGHT;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const phoneQuery = window.matchMedia(PHONE_MEDIA_QUERY);
    const gripQuery = window.matchMedia(GRIP_MEDIA_QUERY);

    const isLeft = position === "left";
    const sideProp = isLeft ? "left" : "right";
    const windowAlign = isLeft ? "flex-start" : "flex-end";

    const chatIcon = (): string =>
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      `<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" stroke="${launcherGlyph}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` +
      "</svg>";

    const closeIcon = (stroke: string, size: number): string =>
      `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" aria-hidden="true">` +
      `<line x1="18" y1="6" x2="6" y2="18" stroke="${stroke}" stroke-width="2" stroke-linecap="round"/>` +
      `<line x1="6" y1="6" x2="18" y2="18" stroke="${stroke}" stroke-width="2" stroke-linecap="round"/>` +
      "</svg>";

    const container = document.createElement("div");
    container.id = "chat9-widget-container";
    container.style.cssText =
      "position:fixed;z-index:9999;display:flex;flex-direction:column;gap:12px;" +
      `align-items:${windowAlign};`;
    document.body.appendChild(container);

    const chatWindow = document.createElement("div");
    chatWindow.id = "chat9-chat-window";
    chatWindow.setAttribute("role", "dialog");
    chatWindow.setAttribute("aria-label", chromeString("panel_name"));
    chatWindow.style.cssText =
      "display:none;position:relative;overflow:hidden;opacity:0;box-sizing:border-box;" +
      `background:${palette.surface};border:1px solid ${palette.line};box-shadow:${palette.shadow};` +
      `transform-origin:${isLeft ? "bottom left" : "bottom right"};`;

    const resizeHandle = document.createElement("button");
    resizeHandle.id = "chat9-resize-handle";
    resizeHandle.type = "button";
    resizeHandle.setAttribute("aria-label", chromeString("resize_chat"));
    const handleCorner = isLeft ? "top:0;right:0;" : "top:0;left:0;";
    const handleCursor = isLeft ? "ne-resize" : "nw-resize";
    resizeHandle.style.cssText =
      "all:unset;position:absolute;" +
      handleCorner +
      `box-sizing:border-box;width:${GRIP_SIZE}px;height:${GRIP_SIZE}px;background:transparent;` +
      `cursor:${handleCursor};z-index:20;display:none;align-items:center;justify-content:center;` +
      `color:${CHROME_FG_MUTED};opacity:0;` +
      (reducedMotion ? "" : `transition:opacity ${GRIP_FADE_MS}ms ease-out;`);
    resizeHandle.innerHTML =
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      (isLeft
        ? '<path d="M11 19H5v-6"/><path d="M13 5h6v6"/><path d="M19 5 5 19"/>'
        : '<path d="M19 13v6h-6"/><path d="M5 11V5h6"/><path d="m5 5 14 14"/>') +
      "</svg>";

    const iframe = makeIframe(false);

    const resizeOverlay = document.createElement("div");
    resizeOverlay.style.cssText =
      "display:none;position:fixed;top:0;left:0;right:0;bottom:0;z-index:10000;cursor:" +
      handleCursor +
      ";";

    const fab = document.createElement("button");
    fab.id = "chat9-fab";
    fab.type = "button";
    fab.setAttribute("aria-controls", "chat9-chat-window");
    fab.style.cssText =
      `width:56px;height:56px;border-radius:50%;border:none;background:${launcherFill};cursor:pointer;` +
      "display:flex;align-items:center;justify-content:center;flex-shrink:0;padding:0;" +
      `box-shadow:${LAUNCHER_SHADOW};` +
      (reducedMotion ? "" : "transition:background-color 120ms ease-out, filter 120ms ease-out;");

    function setLauncherState(background: string, filter: string) {
      fab.style.background = background;
      fab.style.filter = filter;
    }
    function launcherRest() {
      setLauncherState(launcherFill, "");
    }
    function launcherHover() {
      setLauncherState(accentRgb ? launcherFill : LAUNCHER_HOVER_FILL, launcherHoverFilter);
    }
    function launcherActive() {
      setLauncherState(accentRgb ? launcherFill : LAUNCHER_ACTIVE_FILL, launcherActiveFilter);
    }
    fab.addEventListener("mouseenter", launcherHover);
    fab.addEventListener("mouseleave", launcherRest);
    fab.addEventListener("mousedown", launcherActive);
    fab.addEventListener("mouseup", launcherHover);
    fab.addEventListener("focus", () => {
      if (fab.matches(":focus-visible")) fab.style.boxShadow = `${LAUNCHER_FOCUS_RING}, ${LAUNCHER_SHADOW}`;
    });
    fab.addEventListener("blur", () => {
      fab.style.boxShadow = LAUNCHER_SHADOW;
    });

    fab.setAttribute("aria-label", chromeString("open_chat"));
    fab.innerHTML = chatIcon();

    let panelWidth = `${DEFAULT_PANEL_WIDTH}px`;
    let panelHeight = `${DEFAULT_PANEL_HEIGHT}px`;

    function applyLayout() {
      const phone = phoneQuery.matches;
      const inset = phone ? 16 : PANEL_OFFSET;
      container.style.bottom = `max(${inset}px, env(safe-area-inset-bottom))`;
      container.style[sideProp] = `max(${inset}px, env(safe-area-inset-${sideProp}))`;
      const fullScreen = isOpen && phone;
      if (fullScreen) lockBodyScroll();
      else unlockBodyScroll();
      fab.style.display = isOpen ? "none" : "flex";
      chatWindow.setAttribute("aria-modal", String(fullScreen));
      resizeHandle.style.display = !phone && gripQuery.matches ? "flex" : "none";
      const grip = isOpen && !phone && gripQuery.matches;
      if (grip !== gripShown) {
        gripShown = grip;
        if (handles) postPanelStateToIframe(handles, isOpen, fullScreen, grip);
      }
      if (fullScreen) {
        const viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
        Object.assign(chatWindow.style, {
          position: "fixed",
          top: "0",
          left: "0",
          right: "0",
          bottom: "0",
          width: "auto",
          height: `${viewportHeight}px`,
          minWidth: "0",
          minHeight: "0",
          maxWidth: "none",
          maxHeight: "none",
          border: "none",
          borderRadius: "0",
          boxShadow: "none",
        });
      } else {
        Object.assign(chatWindow.style, {
          position: "relative",
          top: "auto",
          left: "auto",
          right: "auto",
          bottom: "auto",
          width: panelWidth,
          height: panelHeight,
          minWidth: `${MIN_PANEL_WIDTH}px`,
          minHeight: `${MIN_PANEL_HEIGHT}px`,
          maxWidth: `min(${MAX_PANEL_WIDTH}px, calc(100vw - ${2 * PANEL_OFFSET}px))`,
          maxHeight: `min(${MAX_PANEL_HEIGHT}px, calc(100vh - ${2 * PANEL_OFFSET}px - ${topClearance}px))`,
          border: `1px solid ${palette.line}`,
          borderRadius: PANEL_RADIUS,
          boxShadow: palette.shadow,
        });
      }
    }

    function setPanelMotion(open: boolean) {
      const hidden = phoneQuery.matches ? "translateY(100%)" : "translateY(8px)";
      const duration = open ? 200 : 120;
      chatWindow.style.transition = reducedMotion
        ? "none"
        : `opacity ${duration}ms ease-out, transform ${duration}ms ease-out`;
      chatWindow.style.opacity = open ? "1" : "0";
      chatWindow.style.transform = open ? "translateY(0)" : hidden;
    }

    function lockBodyScroll() {
      if (bodyScrollLocked) return;
      bodyScrollLocked = true;
      savedBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    function unlockBodyScroll() {
      if (!bodyScrollLocked) return;
      bodyScrollLocked = false;
      document.body.style.overflow = savedBodyOverflow;
    }

    function onViewportResize() {
      if (isOpen) applyLayout();
    }

    const failOverlay = document.createElement("div");
    failOverlay.style.cssText =
      "display:none;position:absolute;top:0;left:0;right:0;bottom:0;z-index:30;flex-direction:column;" +
      `align-items:center;justify-content:center;gap:16px;background:${palette.surface};color:${palette.fg};` +
      "font:400 14px/20px system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;text-align:center;padding:16px;";
    const failText = document.createElement("div");
    const failRetry = document.createElement("button");
    failRetry.type = "button";
    failRetry.style.cssText =
      "height:44px;padding:0 16px;border-radius:8px;cursor:pointer;font-family:inherit;font-size:14px;font-weight:500;line-height:20px;" +
      `background:${palette.surface};color:${palette.fg};border:1px solid ${palette.lineStrong};`;
    failRetry.textContent = chromeString("try_again_button");
    const failClose = document.createElement("button");
    failClose.type = "button";
    failClose.setAttribute("aria-label", chromeString("close_chat"));
    failClose.style.cssText =
      "position:absolute;top:0;right:0;width:44px;height:44px;border:none;background:transparent;" +
      `cursor:pointer;display:flex;align-items:center;justify-content:center;margin:8px;`;
    failClose.innerHTML = closeIcon(palette.fg, 20);
    failOverlay.appendChild(failClose);
    failOverlay.appendChild(failText);
    failOverlay.appendChild(failRetry);

    function showFailOverlay(message: string, withRetry: boolean) {
      failText.textContent = message;
      failRetry.style.display = withRetry ? "inline-block" : "none";
      failOverlay.style.display = "flex";
    }

    function armLoadFailTimer() {
      window.clearTimeout(loadFailTimer);
      loadFailTimer = window.setTimeout(
        () => showFailOverlay(chromeString("load_failed"), true),
        IFRAME_READY_TIMEOUT_MS,
      );
    }

    failRetry.addEventListener("click", () => {
      showFailOverlay(chromeString("loading"), false);
      iframe.src = buildIframeSrc() + `&_=${Date.now()}`;
      armLoadFailTimer();
    });
    failClose.addEventListener("click", () => closePanel?.());

    onIframeReady = () => {
      window.clearTimeout(loadFailTimer);
      failOverlay.style.display = "none";
    };

    function openChat() {
      isOpen = true;
      panelOpen = true;
      if (!iframe.src) {
        iframe.src = buildIframeSrc();
        armLoadFailTimer();
      }
      chatWindow.style.display = "block";
      applyLayout();
      setPanelMotion(false);
      requestAnimationFrame(() => setPanelMotion(true));
      iframe.focus();
      if (handles) postPanelStateToIframe(handles, true, phoneFullScreen(), gripShown);
    }

    closePanel = () => {
      if (!isOpen) return;
      isOpen = false;
      panelOpen = false;
      if (handles) postPanelStateToIframe(handles, false, false, false);
      gripShown = false;
      setPanelMotion(false);
      window.setTimeout(() => {
        if (isOpen) return;
        chatWindow.style.display = "none";
        applyLayout();
      }, reducedMotion ? 0 : 120);
      panelHovered = false;
      revealGrip(false);
      fab.style.display = "flex";
      fab.focus();
    };

    fab.addEventListener("click", () => {
      openChat();
    });

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !e.defaultPrevented && isOpen && container.contains(document.activeElement)) {
        closePanel?.();
      }
    }

    function setPanelSize(width: number, height: number) {
      panelWidth = Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, width)) + "px";
      panelHeight = Math.max(MIN_PANEL_HEIGHT, Math.min(MAX_PANEL_HEIGHT, height)) + "px";
      chatWindow.style.width = panelWidth;
      chatWindow.style.height = panelHeight;
    }

    let panelHovered = false;
    function revealGrip(visible: boolean) {
      resizeHandle.style.opacity = visible ? "1" : "0";
    }
    chatWindow.addEventListener("mouseenter", () => {
      panelHovered = true;
      revealGrip(true);
    });
    chatWindow.addEventListener("mouseleave", () => {
      panelHovered = false;
      revealGrip(chatWindow.contains(document.activeElement));
    });
    chatWindow.addEventListener("focusin", () => revealGrip(true));
    chatWindow.addEventListener("focusout", (e) => {
      const next = e.relatedTarget;
      revealGrip(panelHovered || (next instanceof Node && chatWindow.contains(next)));
    });
    resizeHandle.addEventListener("mouseenter", () => {
      resizeHandle.style.color = CHROME_FG;
    });
    resizeHandle.addEventListener("mouseleave", () => {
      resizeHandle.style.color = CHROME_FG_MUTED;
    });
    resizeHandle.addEventListener("focus", () => {
      if (resizeHandle.matches(":focus-visible")) resizeHandle.style.outline = `2px solid ${CHROME_ACCENT}`;
    });
    resizeHandle.addEventListener("blur", () => {
      resizeHandle.style.outline = "none";
    });
    resizeHandle.addEventListener("keydown", (e) => {
      if (e.key === "Home") {
        setPanelSize(DEFAULT_PANEL_WIDTH, DEFAULT_PANEL_HEIGHT);
        e.preventDefault();
        return;
      }
      const step = e.shiftKey ? RESIZE_STEP_LARGE : RESIZE_STEP;
      const growsWidth = isLeft ? "ArrowRight" : "ArrowLeft";
      const shrinksWidth = isLeft ? "ArrowLeft" : "ArrowRight";
      let dw = 0;
      let dh = 0;
      if (e.key === growsWidth) dw = step;
      else if (e.key === shrinksWidth) dw = -step;
      else if (e.key === "ArrowUp") dh = step;
      else if (e.key === "ArrowDown") dh = -step;
      else return;
      setPanelSize(chatWindow.offsetWidth + dw, chatWindow.offsetHeight + dh);
      e.preventDefault();
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
      setPanelSize(resizeStartW + dx, resizeStartH + dy);
      e.preventDefault();
    }

    function onMouseUp() {
      if (!isResizing) return;
      isResizing = false;
      resizeOverlay.style.display = "none";
    }

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onViewportResize);
    window.visualViewport?.addEventListener("resize", onViewportResize);

    chatWindow.appendChild(iframe);
    chatWindow.appendChild(failOverlay);
    chatWindow.appendChild(resizeHandle);
    container.appendChild(chatWindow);
    container.appendChild(fab);
    document.body.appendChild(resizeOverlay);
    applyLayout();

    handles = {
      container,
      resizeOverlay,
      iframe,
      widgetOrigin,
      onMessage,
      teardown: () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
        document.removeEventListener("keydown", onKeyDown);
        window.removeEventListener("resize", onViewportResize);
        window.visualViewport?.removeEventListener("resize", onViewportResize);
        window.clearTimeout(loadFailTimer);
        unlockBodyScroll();
      },
    };
    window.addEventListener("message", handles.onMessage);
  }

  function stop() {
    if (!handles) return;
    window.removeEventListener("message", handles.onMessage);
    handles.teardown();
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
