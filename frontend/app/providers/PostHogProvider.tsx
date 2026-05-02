"use client";

import posthog from "posthog-js";
import { PostHogProvider as PHProvider } from "posthog-js/react";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, Suspense } from "react";

// The widget iframe context silently drops all PostHog network egress
// (storage partitioning / extensions / shields). Skip init there so we
// don't write a dead localStorage key and fire dead /decide requests.
const isWidgetPath = (pathname: string | null | undefined) =>
  !!pathname && pathname.startsWith("/widget");

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
  if (typeof window !== "undefined" && isWidgetPath(window.location.pathname)) {
    return <>{children}</>;
  }
  return (
    <PHProvider client={posthog}>
      <Suspense fallback={null}>
        <PostHogPageview />
      </Suspense>
      {children}
    </PHProvider>
  );
}
