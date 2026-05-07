"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { api, type LlmAlertType } from "@/lib/api";

type AlertCopy = {
  title: string;
  body: string;
  cta?: { href: string; label: string };
};

const COPY: Partial<Record<LlmAlertType, AlertCopy>> = {
  quota_exhausted: {
    title: "OpenAI quota exhausted",
    body: "Your OpenAI key is out of credits. Chat9 cannot answer end-user messages until you top up your balance.",
    cta: {
      href: "https://platform.openai.com/settings/organization/billing",
      label: "Top up balance",
    },
  },
  invalid_api_key: {
    title: "OpenAI API key invalid",
    body: "Your OpenAI key was rejected by OpenAI. Update it in Settings → OpenAI to restore chat.",
    cta: { href: "/settings", label: "Update key" },
  },
};

const POLL_INTERVAL_MS = 60_000;

export function LlmAlertBanner() {
  const [alertType, setAlertType] = useState<LlmAlertType | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchAlert() {
      try {
        const data = await api.tenants.getLlmAlert();
        if (cancelled) return;
        setAlertType(data.type);
      } catch {
        // Quietly skip — banner is non-blocking. A 401 (logged out) or
        // 404 (no tenant yet) just means no banner this render.
        if (!cancelled) setAlertType(null);
      }
    }

    void fetchAlert();
    const handle = window.setInterval(fetchAlert, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

  if (!alertType) return null;
  const copy = COPY[alertType];
  if (!copy) return null;

  return (
    <div
      role="alert"
      className="mb-6 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-900"
    >
      <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0" aria-hidden="true" />
      <div className="flex-1">
        <p className="font-semibold">{copy.title}</p>
        <p className="mt-1 text-sm">{copy.body}</p>
        {copy.cta ? (
          <a
            href={copy.cta.href}
            target={copy.cta.href.startsWith("http") ? "_blank" : undefined}
            rel={copy.cta.href.startsWith("http") ? "noopener noreferrer" : undefined}
            className="mt-3 inline-flex items-center rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-amber-700"
          >
            {copy.cta.label}
          </a>
        ) : null}
      </div>
    </div>
  );
}
