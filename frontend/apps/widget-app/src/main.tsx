import { render } from "preact";
import { useCallback, useEffect, useState } from "preact/hooks";
import "./styles.css";
import { ChatWidget } from "./ChatWidget";
import { t } from "./strings";
import { accentOnColor, parseHexColor, type UserHints, type WidgetTheme } from "@chat9/widget-shared";

export type { UserHints };

// undefined = waiting for parent handshake; null = anonymous resolved; object = hints.
type HintsState = UserHints | null | undefined;

// If the loader doesn't respond to chat9:ready within this window, fall back
// to anonymous mode rather than leaving the user staring at a loading frame.
// Picked to be noticeably longer than a sane handshake (sub-100ms) but well
// under user-perceived "is this broken" threshold.
const HINTS_HANDSHAKE_TIMEOUT_MS = 2500;

const HINT_KEYS: (keyof UserHints)[] = [
  "user_id",
  "user_hash",
  "email",
  "name",
  "locale",
  "plan_tier",
  "audience_tag",
];

function coerceHints(raw: unknown): UserHints | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Record<string, unknown>;
  const out: UserHints = {};
  for (const key of HINT_KEYS) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) {
      out[key] = key === "user_hash" ? value.trim().slice(0, 128) : value.trim();
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

/**
 * Validates a full http(s) URL (path/query allowed) for use as a non-fetch
 * link target. Returns undefined if invalid so the consuming prop falls
 * back to its default rather than rendering an attacker-controlled href
 * (e.g. `javascript:` payloads).
 */
function safeMarketingUrl(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  try {
    const u = new URL(raw);
    if (u.protocol !== "https:" && u.protocol !== "http:") return undefined;
    return u.toString();
  } catch {
    return undefined;
  }
}

/**
 * Parse a URL and reduce it to `protocol://host` (origin). Returns null
 * unless the input is an absolute http(s) URL — blocks `javascript:`,
 * `data:`, relative paths, and unparseable junk. Use for any URL coming
 * from an untrusted source (loader query params) before it reaches a
 * `fetch` base or a postMessage `targetOrigin`.
 */
function safeOrigin(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}

function themeFromParam(raw: string | null): WidgetTheme {
  return raw === "dark" ? "dark" : "light";
}

const TENANT_ACCENT_HOVER_FILTER = "brightness(0.92)";
const TENANT_ACCENT_ACTIVE_FILTER = "brightness(0.85)";

function applyTenantAccent(root: HTMLElement, raw: string | null) {
  const accent = parseHexColor(raw);
  if (!accent) return;
  const fill = accent.join(" ");
  const on = parseHexColor(accentOnColor(accent));
  if (!on) return;
  root.style.setProperty("--c-accent", fill);
  root.style.setProperty("--c-accent-hover", fill);
  root.style.setProperty("--c-accent-active", fill);
  root.style.setProperty("--c-accent-on", on.join(" "));
  root.style.setProperty("--accent-hover-filter", TENANT_ACCENT_HOVER_FILTER);
  root.style.setProperty("--accent-active-filter", TENANT_ACCENT_ACTIVE_FILTER);
}

// Set before the first paint so a dark widget never flashes light.
const initialParams = new URLSearchParams(window.location.search);
document.documentElement.dataset.theme = themeFromParam(initialParams.get("theme"));
document.documentElement.dataset.position = initialParams.get("position") === "left" ? "left" : "right";
applyTenantAccent(document.documentElement, initialParams.get("accent"));

function App() {
  const params = initialParams;
  const botId = (params.get("botId") ?? "").trim() || null;
  const locale = params.get("locale") || (typeof navigator !== "undefined" ? navigator.language : null);
  const apiBase = safeOrigin((params.get("apiBase") ?? "").trim());
  const parentOrigin = safeOrigin(params.get("parentOrigin"));
  const embeddedBubble = params.get("mode") === "bubble" && window.parent !== window;
  // siteUrl can be any http(s) URL (path/query allowed for marketing links).
  const siteUrl = safeMarketingUrl(params.get("siteUrl"));

  const [hints, setHints] = useState<HintsState>(undefined);
  const closePanel = useCallback(() => {
    if (parentOrigin) window.parent.postMessage({ type: "chat9:close" }, parentOrigin);
  }, [parentOrigin]);
  // True until the loader says otherwise: a standalone tab or an older loader
  // that never reports the panel behaves as it always did.
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelFullScreen, setPanelFullScreen] = useState(false);

  useEffect(() => {
    // Standalone tab (no embedding iframe) — resolve anonymous immediately.
    if (window.parent === window) {
      setHints(null);
      return;
    }

    // If parentOrigin couldn't be parsed, refuse to postMessage to a wildcard
    // and fall back to anonymous.
    if (!parentOrigin) {
      setHints(null);
      return;
    }

    let firstResolved = false;
    const applyHints = (value: UserHints | null) => {
      // First message clears the handshake timeout; later messages just push
      // new identity into the existing chat.
      if (!firstResolved) {
        firstResolved = true;
        window.clearTimeout(timer);
      }
      setHints(value);
    };

    function handleMessage(event: MessageEvent) {
      if (event.source !== window.parent) return;
      if (event.origin !== parentOrigin) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "chat9:hints") {
        applyHints(coerceHints((data as { userHints?: unknown }).userHints));
      } else if (data.type === "chat9:no-hints") {
        applyHints(null);
      } else if (data.type === "chat9:panel") {
        setPanelOpen(Boolean((data as { open?: unknown }).open));
        setPanelFullScreen(Boolean((data as { fullScreen?: unknown }).fullScreen));
      }
    }
    window.addEventListener("message", handleMessage);

    // Tell the loader we're mounted — it responds with chat9:hints (object)
    // or chat9:no-hints (anonymous).
    window.parent.postMessage({ type: "chat9:ready" }, parentOrigin);

    // Fail-safe: if the loader is broken or never sends a response, don't
    // hang on "Loading…" forever — degrade to anonymous after a short delay.
    // Only applies until first resolution; later updates have no timeout.
    const timer = window.setTimeout(() => {
      if (!firstResolved) applyHints(null);
    }, HINTS_HANDSHAKE_TIMEOUT_MS);

    return () => {
      window.removeEventListener("message", handleMessage);
      window.clearTimeout(timer);
    };
  }, [parentOrigin]);

  if (!botId || !apiBase) {
    console.error("Chat9: missing required loader parameters botId and apiBase.");
    return (
      <div className="flex h-screen w-full items-center justify-center bg-surface px-4 text-ui text-fg">
        {t(locale, "unavailable")}
      </div>
    );
  }

  if (hints === undefined) {
    return (
      <div role="status" className="flex h-screen w-full items-center justify-center bg-surface px-4 text-ui text-fg-muted">
        {t(locale, "loading")}
      </div>
    );
  }

  // Identity key — when the loader pushes a different user (login / user
  // switch / logout), React remounts ChatWidget so per-conversation state
  // (messages, sessionId, ticket) resets cleanly instead of leaking from
  // the previous identity.
  const identityKey = hints?.user_id ?? hints?.email ?? "anon";

  return (
    <div className="flex h-screen w-full">
      <ChatWidget
        key={identityKey}
        botId={botId}
        locale={locale}
        hints={hints}
        isOpen={panelOpen}
        fullScreen={panelFullScreen}
        apiBase={apiBase}
        siteUrl={siteUrl}
        onClose={embeddedBubble && parentOrigin ? closePanel : undefined}
      />
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  render(<App />, root);
}
