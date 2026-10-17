"use client";

import { useActiveBot } from "@/hooks/useApi";
import { CopyButton } from "@/components/ui/copy-button";
import { CodeValue } from "./CodeValue";
import { SectionFallback, SettingsSection } from "../../settings/_components/SettingsSection";

export function BotIdSection() {
  const { bots, activeBot, error, mutate } = useActiveBot();

  return (
    <SettingsSection title="Bot ID">
      {activeBot ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <CodeValue>{activeBot.public_id}</CodeValue>
            <CopyButton text={activeBot.public_id} label="Copy" variant="secondary" />
          </div>
          <p className="text-caption text-fg-muted">Use it as data-bot-id in the embed snippet.</p>
        </>
      ) : (
        <SectionFallback failed={!bots && Boolean(error)} what="the bot ID" fields={1} onRetry={() => mutate()} />
      )}
    </SettingsSection>
  );
}
