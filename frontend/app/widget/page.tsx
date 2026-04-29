"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ChatWidget } from "@/components/ChatWidget";

// undefined = waiting for parent handshake; null = anonymous resolved; string = identified.
type IdentityState = string | null | undefined;

function WidgetContent() {
  const searchParams = useSearchParams();
  const botId = searchParams.get("botId");
  const locale = searchParams.get("locale") || (typeof window !== "undefined" ? navigator.language : null);
  // embed.js stamps the embedding page's origin into the iframe URL so we can
  // postMessage back with an explicit target instead of a wildcard.
  const parentOriginParam = searchParams.get("parentOrigin");
  const [identityToken, setIdentityToken] = useState<IdentityState>(undefined);

  useEffect(() => {
    if (typeof window === "undefined") return;

    // Standalone tab (no embedding iframe) — resolve anonymous immediately.
    if (window.parent === window) {
      setIdentityToken(null);
      return;
    }

    // Validate parentOrigin: must be a syntactically-correct origin matching
    // what document.referrer suggests. If anything looks off, fall back to
    // anonymous rather than postMessage to a wildcard.
    let parentOrigin: string | null = null;
    try {
      if (parentOriginParam) {
        const u = new URL(parentOriginParam);
        parentOrigin = `${u.protocol}//${u.host}`;
      }
    } catch {
      parentOrigin = null;
    }
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

    // Signal to embed.js that the widget is mounted and ready to receive identity.
    // embed.js responds with chat9:identity (token) or chat9:no-identity (anonymous).
    window.parent.postMessage({ type: "chat9:ready" }, parentOrigin);

    return () => window.removeEventListener("message", handleMessage);
  }, [parentOriginParam]);

  if (!botId) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 font-['Inter']">
        <div className="max-w-md rounded-[28px] border border-[#DCE5F2] bg-white px-6 py-7 text-center shadow-[0_24px_80px_rgba(15,23,42,0.12)]">
          <h1 className="text-xl font-semibold tracking-[-0.03em] text-[#0F172A]">Invalid bot ID</h1>
          <p className="mt-3 text-sm leading-6 text-[#64748B]">
            Виджет не может стартовать без публичного идентификатора бота.
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
      <ChatWidget botId={botId} locale={locale} identityToken={identityToken} />
    </div>
  );
}

export default function WidgetPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 text-sm text-[#64748B]">
          Loading...
        </div>
      }
    >
      <WidgetContent />
    </Suspense>
  );
}
