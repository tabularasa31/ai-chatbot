"use client";

import { BotRequired } from "./_components/BotRequired";
import { DeleteTenantSection } from "./_components/DeleteTenantSection";
import { EscalationSection } from "./_components/EscalationSection";
import { InstructionsSection } from "./_components/InstructionsSection";
import { OpenAiKeySection } from "./_components/OpenAiKeySection";
import { ResponseDetailSection } from "./_components/ResponseDetailSection";
import { SettingsShell } from "./_components/SettingsShell";

export default function SettingsPage() {
  return (
    <SettingsShell active="bot">
      <div className="flex flex-col gap-8">
        <OpenAiKeySection />
        <BotRequired>
          <InstructionsSection />
          <ResponseDetailSection />
        </BotRequired>
        <EscalationSection />
        <DeleteTenantSection />
      </div>
    </SettingsShell>
  );
}
