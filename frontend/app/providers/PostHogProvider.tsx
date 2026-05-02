"use client";

import posthog from "posthog-js";
import { PostHogProvider as PHProvider } from "posthog-js/react";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, Suspense } from "react";

// The widget iframe context silently drops all PostHog network egress
// (storage partitioning / extensions / shields). Skip init there so we
// don't write a dead localStorage key and fire dead /decide requests.
// Match exactly /widget or its subpaths — not /widget-settings (a real
// authenticated admin route in the dashboard).
const isWidgetPath = (pathname: string | null | undefined) =>
  !!pathname && (pathname === "/widget" || pathname.startsWith("/widget/"));

if (typeof window !== "undefined" && !isWidgetPath(window.location.pathname)) {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (key) {
    posthog.init(key, {
      api_host: "/ingest",
      ui_host: "https://eu.posthog.com",
      capture_pageview: false,
      capture_pageleave: true,
      session_recording: { maskAllInputs: true },
    });
  }
}

function PostHogPageview() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (!pathname || isWidgetPath(pathname)) return;
    const url =
      window.location.origin +
      pathname +
      (searchParams.toString() ? `?${searchParams.toString()}` : "");
    posthog.capture("$pageview", { $current_url: url });
  }, [pathname, searchParams]);

  return null;
}

export function PostHogProvider({ children }: { children: React.ReactNode }) {
  // Always render PHProvider — without init it's an inert Context wrapper.
  // Gating it here on `window.location.pathname` would hydration-mismatch
  // (server has no window; usePathname() returns null on server). The dead
  // side-effects (init + $pageview) are already gated in client-only code
  // above, where hydration doesn't observe them.
  return (
    <PHProvider client={posthog}>
      <Suspense fallback={null}>
        <PostHogPageview />
      </Suspense>
      {children}
    </PHProvider>
  );
}
