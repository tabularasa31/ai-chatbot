"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ensureLoaderScript } from "@/lib/widget-embed";
import { standaloneLinkClasses } from "./classes";

export const HOW_THE_BOT_ANSWERS_HREF = "/blog/how-chat9-ai-bot-works";

const TARGET_ID = "chat9-landing-demo";
const SKELETON_DELAY_MS = 200;
const LOAD_TIMEOUT_MS = 10_000;

type DemoStatus = "loading" | "ready" | "error";

function DemoErrorMessage() {
  return (
    <>
      <p className="text-body font-semibold text-fg">The demo did not load.</p>
      <p className="mt-2 text-body text-fg">
        Reload the page to try again, or read how the bot answers questions.
      </p>
    </>
  );
}

function DemoSkeleton() {
  return (
    <div className="absolute inset-0 flex flex-col justify-between bg-surface p-4" aria-hidden>
      <div className="h-11 w-1/2 rounded-control bg-surface-alt" />
      <div className="space-y-3">
        <div className="h-16 w-4/5 rounded-bubble bg-surface-alt" />
        <div className="ml-auto h-11 w-3/5 rounded-bubble bg-surface-alt" />
      </div>
      <div className="h-11 rounded-control bg-surface-alt" />
    </div>
  );
}

export function DemoSlot({ botId }: { botId: string }) {
  const [status, setStatus] = useState<DemoStatus>("loading");
  const [skeletonVisible, setSkeletonVisible] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const skeletonTimer = setTimeout(() => setSkeletonVisible(true), SKELETON_DELAY_MS);
    const failTimer = setTimeout(() => {
      cancelled = true;
      setStatus("error");
    }, LOAD_TIMEOUT_MS);

    ensureLoaderScript(botId)
      .then(() => {
        const widget = window.Chat9Widget;
        if (cancelled || !widget) return;
        clearTimeout(failTimer);
        if (!widget.isStarted()) {
          // The loader's baked-in apiBase is production; preview hosts must call their own origin.
          widget.start({ mode: "inline", target: TARGET_ID, apiBase: window.location.origin });
        }
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });

    return () => {
      cancelled = true;
      clearTimeout(skeletonTimer);
      clearTimeout(failTimer);
      window.Chat9Widget?.stop();
      const target = document.getElementById(TARGET_ID);
      if (target) target.innerHTML = "";
    };
  }, [botId]);

  return (
    <div>
      <p className="mb-2 text-caption text-fg-muted">
        Live demo: the real widget, answering from the Chat9 documentation.
      </p>
      <section
        aria-label="Live demo"
        aria-busy={status === "loading"}
        className="relative h-demo sm:h-demo-sm overflow-hidden rounded-surface border border-line bg-surface"
      >
        {status === "error" ? (
          <div className="p-6">
            <DemoErrorMessage />
            <div className="mt-4 flex flex-wrap items-center gap-x-4">
              <Button variant="secondary" offset="surface" onClick={() => window.location.reload()}>
                Reload page
              </Button>
              <Link href={HOW_THE_BOT_ANSWERS_HREF} className={standaloneLinkClasses}>
                How the bot answers
              </Link>
            </div>
          </div>
        ) : (
          <>
            <div id={TARGET_ID} className="h-full w-full" />
            {status === "loading" && skeletonVisible && <DemoSkeleton />}
          </>
        )}
        <noscript>
          <div className="absolute inset-0 bg-surface p-6">
            <DemoErrorMessage />
            <Link href={HOW_THE_BOT_ANSWERS_HREF} className={standaloneLinkClasses}>
              How the bot answers
            </Link>
          </div>
        </noscript>
      </section>
    </div>
  );
}
