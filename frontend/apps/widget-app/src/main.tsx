import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import "./styles.css";
import { ChatWidget } from "./ChatWidget";
import { t } from "./strings";

export type UserHints = {
  user_id?: string;
  email?: string;
  name?: string;
  locale?: string;
  plan_tier?: string;
  audience_tag?: string;
};

// undefined = waiting for parent handshake; null = anonymous resolved; object = hints.
type HintsState = UserHints | null | undefined;

// If the loader doesn't respond to chat9:ready within this window, fall back
// to anonymous mode rather than leaving the user staring at a loading frame.
// Picked to be noticeably longer than a sane handshake (sub-100ms) but well
// under user-perceived "is this broken" threshold.
const HINTS_HANDSHAKE_TIMEOUT_MS = 2500;

const HINT_KEYS: (keyof UserHints)[] = [
  "user_id",
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
      out[key] = value.trim();
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

function App() {
  const params = new URLSearchParams(window.location.search);
  const botId = (params.get("botId") ?? "").trim() || null;
  const locale = params.get("locale") || (typeof navigator !== "undefined" ? navigator.language : null);
  const apiBase = safeOrigin((params.get("apiBase") ?? "").trim());
  const parentOrigin = safeOrigin(params.get("parentOrigin"));
  // siteUrl can be any http(s) URL (path/query allowed for marketing links).
  const siteUrl = safeMarketingUrl(params.get("siteUrl"));

  const [hints, setHints] = useState<HintsState>(undefined);

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

    let resolved = false;
    const resolve = (value: UserHints | null) => {
      if (resolved) return;
      resolved = true;
      setHints(value);
    };

    function handleMessage(event: MessageEvent) {
      if (event.source !== window.parent) return;
      if (event.origin !== parentOrigin) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "chat9:hints") {
        resolve(coerceHints((data as { userHints?: unknown }).userHints));
      } else if (data.type === "chat9:no-hints") {
        resolve(null);
      }
    }
    window.addEventListener("message", handleMessage);

    // Tell the loader we're mounted — it responds with chat9:hints (object)
    // or chat9:no-hints (anonymous).
    window.parent.postMessage({ type: "chat9:ready" }, parentOrigin);

    // Fail-safe: if the loader is broken or never sends a response, don't
    // hang on "Loading…" forever — degrade to anonymous after a short delay.
    const timer = window.setTimeout(() => resolve(null), HINTS_HANDSHAKE_TIMEOUT_MS);

    return () => {
      window.removeEventListener("message", handleMessage);
      window.clearTimeout(timer);
    };
  }, [parentOrigin]);

  if (!botId || !apiBase) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 font-['Inter']">
        <div className="max-w-md rounded-[28px] border border-[#DCE5F2] bg-white px-6 py-7 text-center shadow-[0_24px_80px_rgba(15,23,42,0.12)]">
          <h1 className="text-xl font-semibold tracking-[-0.03em] text-[#0F172A]">{t(locale, "misconfigured_title")}</h1>
          <p className="mt-3 text-sm leading-6 text-[#64748B]">{t(locale, "misconfigured_body")}</p>
        </div>
      </div>
    );
  }

  if (hints === undefined) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 text-sm text-[#64748B] font-['Inter']">
        {t(locale, "loading")}
      </div>
    );
  }

  return (
    <div className="flex h-screen w-full font-['Inter']">
      <ChatWidget
        botId={botId}
        locale={locale}
        hints={hints}
        apiBase={apiBase}
        siteUrl={siteUrl}
      />
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  render(<App />, root);
}
