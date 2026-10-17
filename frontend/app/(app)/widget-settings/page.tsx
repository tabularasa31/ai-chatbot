"use client";

import { BotRequired } from "../settings/_components/BotRequired";
import { SettingsShell } from "../settings/_components/SettingsShell";
import { BotIdSection } from "./_components/BotIdSection";
import { IdentityKeySection } from "./_components/IdentityKeySection";
import { LinkSafetySection } from "./_components/LinkSafetySection";

export default function WidgetSettingsPage() {
  return (
    <SettingsShell active="widget">
      <div className="flex flex-col gap-8">
        <BotRequired>
          <BotIdSection />
          <LinkSafetySection />
        </BotRequired>
        <IdentityKeySection />
      </div>
    </SettingsShell>
  );
}
