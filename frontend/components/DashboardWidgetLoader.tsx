"use client";
import { useEffect } from "react";
import { api } from "@/lib/api";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;
const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

// Must match the fixed navbar height defined in (app)/layout.tsx.
const TOP_CLEARANCE = 56;

type UserHints = {
  user_id?: string;
  email?: string;
  name?: string;
  locale?: string;
  plan_tier?: string;
  audience_tag?: string;
};

type Chat9WidgetApi = {
  start: (config?: Record<string, unknown>) => void;
  stop: () => void;
  setHints: (hints: UserHints | null) => void;
  destroy: () => void;
};

type WindowWithWidget = Window & { Chat9Widget?: Chat9WidgetApi };

// Module-scope guard: the loader script lives on the page once per browser
// session even if React mounts/unmounts this component.
let scriptReady: Promise<void> | null = null;

function ensureLoaderScript(): Promise<void> {
  if (scriptReady) return scriptReady;
  if ((window as WindowWithWidget).Chat9Widget) {
    scriptReady = Promise.resolve();
    return scriptReady;
  }
  scriptReady = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = WIDGET_LOADER_URL;
    script.async = true;
    script.setAttribute("data-bot-id", BOT_ID!);
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Chat9: failed to load widget.js"));
    document.body.appendChild(script);
  });
  return scriptReady;
}

export function DashboardWidgetLoader() {
  useEffect(() => {
    if (!BOT_ID) return;

    let cancelled = false;

    function startWithHints(hints: UserHints | null) {
      const w = window as WindowWithWidget;
      if (!w.Chat9Widget) return;
      w.Chat9Widget.start({
        apiBase: window.location.origin,
        color: "#a855f7",
        topClearance: TOP_CLEARANCE,
        ...(hints ? { userHints: hints } : {}),
      });
    }

    ensureLoaderScript()
      .then(() => api.auth.getMe().catch(() => null))
      .then((user) => {
        if (cancelled) return;
        const hints: UserHints | null = user?.email ? { email: user.email } : null;
        startWithHints(hints);
      })
      .catch(() => {
        if (cancelled) return;
        startWithHints(null);
      });

    return () => {
      cancelled = true;
      // stop() is the soft teardown — script and Chat9Widget stay registered
      // so the next mount can call start() again without re-downloading.
      const w = window as WindowWithWidget;
      w.Chat9Widget?.stop();
    };
  }, []);

  return null;
}
