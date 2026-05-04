import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import "./styles.css";
import { ChatWidget } from "./ChatWidget";

// undefined = waiting for parent handshake; null = anonymous resolved; string = identified.
type IdentityState = string | null | undefined;

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

  const [identityToken, setIdentityToken] = useState<IdentityState>(undefined);

  useEffect(() => {
    // Standalone tab (no embedding iframe) — resolve anonymous immediately.
    if (window.parent === window) {
      setIdentityToken(null);
      return;
    }

    // If parentOrigin couldn't be parsed, refuse to postMessage to a wildcard
    // and fall back to anonymous.
    if (!parentOrigin) {
      setIdentityToken(null);
      return;
    }

    function handleMessage(event: MessageEvent) {
      if (event.source !== window.parent) return;
      if (event.origin !== parentOrigin) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "chat9:identity" && typeof data.identityToken === "string") {
        setIdentityToken(data.identityToken);
      } else if (data.type === "chat9:no-identity") {
        setIdentityToken(null);
      }
    }
    window.addEventListener("message", handleMessage);

    // Tell the loader we're mounted — it responds with chat9:identity (token)
    // or chat9:no-identity (anonymous).
    window.parent.postMessage({ type: "chat9:ready" }, parentOrigin);

    return () => window.removeEventListener("message", handleMessage);
  }, [parentOrigin]);

  if (!botId || !apiBase) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 font-['Inter']">
        <div className="max-w-md rounded-[28px] border border-[#DCE5F2] bg-white px-6 py-7 text-center shadow-[0_24px_80px_rgba(15,23,42,0.12)]">
          <h1 className="text-xl font-semibold tracking-[-0.03em] text-[#0F172A]">Widget misconfigured</h1>
          <p className="mt-3 text-sm leading-6 text-[#64748B]">
            Missing required loader parameters: botId and apiBase.
          </p>
        </div>
      </div>
    );
  }

  if (identityToken === undefined) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 text-sm text-[#64748B] font-['Inter']">
        Loading...
      </div>
    );
  }

  return (
    <div className="flex h-screen w-full font-['Inter']">
      <ChatWidget
        botId={botId}
        locale={locale}
        identityToken={identityToken}
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
