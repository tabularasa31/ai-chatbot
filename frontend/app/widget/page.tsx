"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ChatWidget } from "@/components/ChatWidget";

function WidgetContent() {
  const searchParams = useSearchParams();
  const clientId = searchParams.get("clientId");
  const locale =
    searchParams.get("locale") ||
    (typeof window !== "undefined" ? navigator.language : null);

  if (!clientId) {
    return (
      <div
        style={{
          padding: "20px",
          textAlign: "center",
          color: "#666",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        Invalid client ID
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: "#fff",
      }}
    >
      <ChatWidget clientId={clientId} locale={locale} />
    </div>
  );
}

export default function WidgetPage() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            padding: "20px",
            textAlign: "center",
            color: "#999",
            fontFamily: "system-ui, sans-serif",
          }}
        >
          Loading...
        </div>
      }
    >
      <WidgetContent />
    </Suspense>
  );
}
