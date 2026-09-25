"use client";
import { useEffect } from "react";
import { api } from "@/lib/api";
import type { UserHints, WindowWithChat9Widget } from "@/types/chat9-widget";
import { ensureLoaderScript } from "@/lib/widget-embed";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;

// Must match the fixed navbar height defined in (app)/layout.tsx.
const TOP_CLEARANCE = 56;

export function DashboardWidgetLoader({
  email = null,
}: {
  /** Session email decoded server-side in the layout; when present, skips the /auth/me fetch. */
  email?: string | null;
}) {
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

    ensureLoaderScript(BOT_ID!)
      .then(() =>
        email ? { email } : api.auth.getMe().catch(() => null),
      )
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
  }, [email]);

  return null;
}
