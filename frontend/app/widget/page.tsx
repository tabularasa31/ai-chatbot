"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ChatWidget } from "@/components/ChatWidget";

function WidgetContent() {
  const searchParams = useSearchParams();
  const clientId = searchParams.get("clientId");
  const locale = searchParams.get("locale") || (typeof window !== "undefined" ? navigator.language : null);

  if (!clientId) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F1F5F9_100%)] px-4 font-['Inter']">
        <div className="max-w-md rounded-[28px] border border-[#DCE5F2] bg-white px-6 py-7 text-center shadow-[0_24px_80px_rgba(15,23,42,0.12)]">
          <h1 className="text-xl font-semibold tracking-[-0.03em] text-[#0F172A]">Invalid client ID</h1>
          <p className="mt-3 text-sm leading-6 text-[#64748B]">
            Виджет не может стартовать без публичного идентификатора клиента.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-start justify-center bg-[linear-gradient(180deg,#F8FBFF_0%,#F3F7FD_100%)] p-3 font-['Inter'] sm:p-4">
      <div className="flex h-[min(72vh,600px)] w-full max-w-4xl min-h-[520px]">
        <ChatWidget clientId={clientId} locale={locale} compact />
      </div>
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
