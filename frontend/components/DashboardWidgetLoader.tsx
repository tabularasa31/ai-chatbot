"use client";
import { useEffect } from "react";
import { api } from "@/lib/api";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;
const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

// Must match the fixed navbar height defined in (app)/layout.tsx.
const TOP_CLEARANCE = 56;

export function DashboardWidgetLoader() {
  useEffect(() => {
    if (!BOT_ID) return;

    let script: HTMLScriptElement | null = null;
    let cancelled = false;

    function inject(email?: string) {
      if (cancelled) return;
      (window as Window & { Chat9Config?: Record<string, unknown> }).Chat9Config = {
        apiBase: window.location.origin,
        color: "#a855f7",
        topClearance: TOP_CLEARANCE,
        ...(email ? { userHints: { email } } : {}),
      };
      script = document.createElement("script");
      script.src = WIDGET_LOADER_URL;
      script.setAttribute("data-bot-id", BOT_ID!);
      document.body.appendChild(script);
    }

    api.auth
      .getMe()
      .then((user) => inject(user.email || undefined))
      .catch(() => inject());

    return () => {
      cancelled = true;
      (window as Window & { Chat9Widget?: { destroy: () => void } }).Chat9Widget?.destroy();
      script?.remove();
      delete (window as Window & { Chat9Config?: unknown }).Chat9Config;
    };
  }, []);

  return null;
}
