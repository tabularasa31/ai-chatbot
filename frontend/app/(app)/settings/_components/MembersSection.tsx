"use client";

import { useState } from "react";
import { api, type TenantMember } from "@/lib/api";
import { useMembers } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { SettingsSection } from "./SettingsSection";

const SKELETON_ROWS = 3;

function roleText(member: TenantMember): string {
  if (member.role === "owner") return "Owner";
  return member.status === "pending" ? "Operator · invite pending" : "Operator";
}

export function MembersSection() {
  const { data, error, mutate } = useMembers();
  const [removing, setRemoving] = useState<TenantMember | null>(null);
  const members = data?.items;
  const onlyOwner = members?.length === 1;

  async function remove(member: TenantMember) {
    await api.members.remove(member.id);
    await mutate();
  }

  return (
    <SettingsSection title="Members" meta={members?.length}>
      {members ? (
        <>
          <ul className="divide-y divide-line">
            {members.map((member) => (
              <li key={member.id} className="flex min-h-11 items-center gap-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="break-anywhere text-ui text-fg">{member.email}</p>
                  <p className="text-caption text-fg-muted">{roleText(member)}</p>
                </div>
                {member.role !== "owner" && (
                  <Button variant="secondary" onClick={() => setRemoving(member)}>
                    Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <p className="text-caption text-fg-muted">
            The owner can&apos;t be removed. To close the tenant, delete it on the Bot tab.
          </p>
          {onlyOwner && <p className="text-ui text-fg-muted">Just you so far. Invite a colleague above.</p>}
        </>
      ) : error ? (
        <LoadError message="Couldn't load the team." onRetry={() => mutate()} />
      ) : (
        <SkeletonRegion>
          {Array.from({ length: SKELETON_ROWS }, (_, index) => (
            <div key={index} className="flex min-h-11 items-center py-2">
              <Skeleton className="w-64 max-w-full" />
            </div>
          ))}
        </SkeletonRegion>
      )}
      {removing && (
        <ConfirmDialog
          title={removing.status === "pending" ? `Cancel the invite for ${removing.email}?` : `Remove ${removing.email}?`}
          body="Their account is deleted and their seat is released. Their past replies stay in transcripts, signed with their email."
          confirmLabel={removing.status === "pending" ? "Cancel invite" : "Remove member"}
          cancelLabel={removing.status === "pending" ? "Keep invite" : undefined}
          tone="destructive"
          failureMessage={`Couldn't remove ${removing.email}. Try again.`}
          onConfirm={() => remove(removing)}
          onClose={() => setRemoving(null)}
        />
      )}
    </SettingsSection>
  );
}
