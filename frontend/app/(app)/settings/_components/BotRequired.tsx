"use client";

import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useActiveBot } from "@/hooks/useApi";
import { SECTION_CLASS } from "./SettingsSection";

/** Shows the no-bot statement once, in place of every section that needs an active bot. */
export function BotRequired({ children }: { children: ReactNode }) {
  const { bots, activeBot, mutate } = useActiveBot();
  const [retrying, setRetrying] = useState(false);

  if (bots === undefined || activeBot) return <>{children}</>;

  async function retry() {
    setRetrying(true);
    try {
      await mutate();
    } finally {
      setRetrying(false);
    }
  }

  return (
    <section className={SECTION_CLASS}>
      <p className="text-ui text-fg-muted">No bot is active, so there is nothing to configure here.</p>
      <Button variant="secondary" busy={retrying} onClick={() => void retry()} className="mt-4">
        Try again
      </Button>
    </section>
  );
}
