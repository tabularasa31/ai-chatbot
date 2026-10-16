"use client";

import { useEffect, useState } from "react";
import { useClientMe } from "@/hooks/useApi";
import { useShellBanner } from "@/components/ShellBanner";
import { InlineNotice } from "@/components/ui/inline-notice";

const DISMISS_KEY = "chat9:widget-identity-banner-dismissed";
const LINK_CLASS = "font-medium text-brand underline underline-offset-2 hover:text-brand-hover";

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

function persistDismissed() {
  try {
    window.localStorage.setItem(DISMISS_KEY, "1");
  } catch {
    // Storage unavailable (private mode): the dismissal lasts until reload.
  }
}

export function WidgetIdentityNotice() {
  const { data: client } = useClientMe();
  const banner = useShellBanner();
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    setDismissed(readDismissed());
  }, []);

  if (!client || client.role !== "owner" || client.has_widget_identity_secret || dismissed || banner) {
    return null;
  }

  return (
    <InlineNotice
      onDismiss={() => {
        persistDismissed();
        setDismissed(true);
      }}
    >
      <p>
        <strong className="font-semibold">Widget identity verification:</strong> verify visitor IDs on your
        server so only the real visitor can reopen their conversation. Optional; needs a server that signs{" "}
        <code className="font-mono">user_id</code>.
      </p>
      <p className="mt-1 flex flex-wrap gap-x-4">
        <a href="/widget-settings#widget-identity-key" className={LINK_CLASS}>
          Set up
        </a>
        <a href="/docs/changelog" className={LINK_CLASS}>
          What changed
        </a>
      </p>
    </InlineNotice>
  );
}
