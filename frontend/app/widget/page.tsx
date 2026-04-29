"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ChatWidget } from "@/components/ChatWidget";

function WidgetContent() {
  const searchParams = useSearchParams();
  const botId = searchParams.get("botId");
  const locale = searchParams.get("locale") || (typeof window !== "undefined" ? navigator.language : null);
  const [identityToken, setIdentityToken] = useState<string | null>(null);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (
        event.data?.type === "chat9:identity" &&
        typeof event.data?.identityToken === "string"
      ) {
        setIdentityToken(event.data.identityToken);
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

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
