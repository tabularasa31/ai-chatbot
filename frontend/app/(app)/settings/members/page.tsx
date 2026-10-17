"use client";

import { InviteSection } from "../_components/InviteSection";
import { MembersSection } from "../_components/MembersSection";
import { SettingsShell } from "../_components/SettingsShell";

export default function MembersPage() {
  return (
    <SettingsShell active="team">
      <div className="flex flex-col gap-8">
        <InviteSection />
        <MembersSection />
      </div>
    </SettingsShell>
  );
}
