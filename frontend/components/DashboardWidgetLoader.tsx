"use client";
import { useEffect } from "react";
import { api } from "@/lib/api";
import type { UserHints, WindowWithChat9Widget } from "@/types/chat9-widget";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;
const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

// Must match the fixed navbar height defined in (app)/layout.tsx.
const TOP_CLEARANCE = 56;

// Module-scope guard: the loader script lives on the page once per browser
// session even if React mounts/unmounts this component. Reset to null below
// if Chat9Widget is gone (e.g. after destroy()) so we re-inject on next mount.
let scriptReady: Promise<void> | null = null;

function ensureLoaderScript(): Promise<void> {
  // If a previous mount cached the promise but Chat9Widget has since been
  // wiped (destroy(), or some other code deleted the global), the cached
  // promise resolves to a stale state where window.Chat9Widget is undefined.
  // Detect and force re-injection.
  if (scriptReady && !(window as WindowWithChat9Widget).Chat9Widget) {
    scriptReady = null;
  }
  if (scriptReady) return scriptReady;
  if ((window as WindowWithChat9Widget).Chat9Widget) {
    scriptReady = Promise.resolve();
    return scriptReady;
  }
  scriptReady = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = WIDGET_LOADER_URL;
    script.async = true;
    script.setAttribute("data-bot-id", BOT_ID!);
    script.onload = () => resolve();
    script.onerror = () => {
      // Reset so a future mount can retry rather than inheriting a rejected
      // promise forever.
      scriptReady = null;
      reject(new Error("Chat9: failed to load widget.js"));
    };
    document.body.appendChild(script);
  });
  return scriptReady;
}

export function DashboardWidgetLoader() {
  useEffect(() => {
    if (!BOT_ID) return;

    let cancelled = false;

    function startWithHints(hints: UserHints | null) {
      const w = window as WindowWithChat9Widget;
      if (!w.Chat9Widget) return;
      if (w.Chat9Widget.isStarted()) {
        // Already mounted from a prior effect (StrictMode double-mount, or a
        // sibling consumer). Just push hints; don't re-mount.
        w.Chat9Widget.setHints(hints);
        return;
      }
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
      const w = window as WindowWithChat9Widget;
      w.Chat9Widget?.stop();
    };
  }, []);

  return null;
}
