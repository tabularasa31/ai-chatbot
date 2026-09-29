"use client";

import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { useClientMe } from "@/hooks/useApi";

const DISMISS_KEY = "chat9:widget-identity-banner-dismissed";

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
    // localStorage unavailable (private mode, quota, etc.) — dismissal just
    // won't survive a reload.
  }
}

export function WidgetIdentityBanner() {
  const { data: client } = useClientMe();
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    setDismissed(readDismissed());
  }, []);

  if (!client || client.role !== "owner" || client.has_widget_identity_secret || dismissed) return null;

  return (
    <div
      role="status"
      className="mb-6 flex items-start gap-3 rounded-lg border border-violet-200 bg-violet-50 p-4 text-violet-900"
    >
      <Info className="mt-0.5 h-5 w-5 flex-shrink-0" aria-hidden="true" />
      <div className="flex-1">
        <p className="font-semibold">Widget identity verification is here</p>
        <p className="mt-1 text-sm">
          Conversations now continue across devices only for verified visitors. Set up identity
          verification if you pass <code className="text-violet-800">user_id</code> to the widget.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm font-medium">
          <a href="/docs/changelog" className="text-violet-700 hover:underline">
            What changed
          </a>
          <a href="/widget-settings#widget-identity-key" className="text-violet-700 hover:underline">
            Set up
          </a>
        </div>
      </div>
      <button
        type="button"
        onClick={() => {
          persistDismissed();
          setDismissed(true);
        }}
        aria-label="Dismiss"
        className="shrink-0 text-violet-500 hover:text-violet-700 px-2 py-1 text-sm"
      >
        Dismiss
      </button>
    </div>
  );
}
