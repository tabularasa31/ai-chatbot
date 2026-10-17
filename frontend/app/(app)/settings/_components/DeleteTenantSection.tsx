"use client";

import { useState } from "react";
import { api, signOutAndLeave } from "@/lib/api";
import { useClientMe } from "@/hooks/useApi";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SettingsSection } from "./SettingsSection";

/** A tenant has one owner and ownership can't move, so deleting it is the owner's only exit. */
export function DeleteTenantSection() {
  const { data: client } = useClientMe();
  const [confirming, setConfirming] = useState(false);

  if (!client?.name) return null;
  const { id, name } = client;

  async function deleteTenant() {
    await api.clients.delete(id);
    signOutAndLeave("workspace_deleted");
  }

  return (
    <SettingsSection title="Delete tenant">
      <p className="text-ui text-fg">
        A tenant has one owner and ownership can&apos;t be transferred, so deleting it is the only way to leave.
      </p>
      <Button variant="secondary" onClick={() => setConfirming(true)}>
        Delete tenant
      </Button>
      {confirming && (
        <ConfirmDialog
          title={`Delete ${name}?`}
          body={
            <div className="space-y-3">
              <p>This happens immediately and can&apos;t be undone.</p>
              <ul className="list-disc space-y-1 pl-5">
                <li>The widget stops answering on your site.</li>
                <li>
                  Everything we hold for this tenant is deleted: conversations, tickets, documents and indexed
                  content.
                </li>
                <li>
                  Your account and every invited colleague&apos;s account are deleted, and you are signed out.
                </li>
              </ul>
              <p>Copies you exported or saved elsewhere aren&apos;t affected.</p>
            </div>
          }
          confirmLabel="Delete tenant"
          confirmText={name}
          tone="destructive"
          failureMessage="Couldn't delete the tenant. Try again."
          onConfirm={deleteTenant}
          onClose={() => setConfirming(false)}
        />
      )}
    </SettingsSection>
  );
}
