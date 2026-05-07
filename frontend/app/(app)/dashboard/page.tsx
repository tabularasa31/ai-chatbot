"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { clearSession, api } from "@/lib/api";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";
import { useTenantMe, useBots } from "@/hooks/useApi";

const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ||
  (typeof window !== "undefined" ? window.location.origin : "");
const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

function DashboardContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const showVerificationBanner = searchParams.get("verification_sent") === "1";

  const { data: client, error: clientError, isLoading: clientLoading } = useTenantMe();
  const { data: bots, isLoading: botsLoading } = useBots();

  const firstActiveBot = bots?.find((b) => b.is_active) ?? bots?.[0];
  const botPublicId = firstActiveBot?.public_id ?? null;

  useEffect(() => {
    if (!clientError) return;
    const msg = clientError instanceof Error ? clientError.message : "";
    if (msg.toLowerCase().includes("email not verified")) {
      clearSession();
      api.auth.logout();
      router.replace("/login?error=email_not_verified");
    }
  }, [clientError, router]);

  function getEmbedSnippet() {
    // Loader lives on a CDN-style domain (widget.getchat9.live), separate from
    // the dashboard. apiBase tells the widget where to send /widget/chat etc.
    // — defaults to the production dashboard, override on staging via
    // NEXT_PUBLIC_APP_URL during snippet rendering.
    const configBlock =
      APP_URL && APP_URL !== "https://getchat9.live"
        ? `<script>\n  window.Chat9Config = { apiBase: ${JSON.stringify(APP_URL)} };\n</script>\n`
        : "";
    return `${configBlock}<script\n  src="${WIDGET_LOADER_URL}"\n  data-bot-id="${botPublicId ?? ""}">\n</script>`;
  }

  if (clientLoading || botsLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    );
  }

  if (clientError && !(clientError instanceof Error && clientError.message.toLowerCase().includes("email not verified"))) {
    return (
      <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg">
        {clientError instanceof Error ? clientError.message : "Failed to load"}
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {showVerificationBanner && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded-lg text-sm">
          We sent a verification link to your email. Please check your inbox and click the link to verify your account.
        </div>
      )}
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Dashboard</h1>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        {botPublicId && (
          <div className="mb-6">
            <h2 className="text-base font-semibold text-slate-800 mb-1">Your Bot ID</h2>
            <p className="mb-2 text-sm text-slate-500">
              Public bot identifier used in the widget snippet.
            </p>
            <code className="flex-1 min-w-0 px-3 py-2 bg-slate-100 rounded-lg text-sm text-slate-800 break-all font-mono">
              {botPublicId}
            </code>
          </div>
        )}
        <h2 className="text-base font-semibold text-slate-800 mb-1">Your widget API key</h2>
        <p className="text-slate-500 text-sm mb-3">
          The plaintext key is only shown once at creation or rotation. Identify
          the active key by its last 4 characters; rotate it from{" "}
          <Link href="/settings/api-keys" className="underline font-medium">Settings → API keys</Link>.
        </p>
        <div className="flex items-center gap-2 flex-wrap">
          <code className="flex-1 min-w-0 px-3 py-2 bg-slate-100 rounded-lg text-sm text-slate-800 break-all font-mono">
            {client?.api_key_hint ? `ck_…${client.api_key_hint}` : "—"}
          </code>
          <Link
            href="/settings/api-keys"
            className="px-4 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-700 transition-colors"
          >
            Manage
          </Link>
        </div>
      </div>

      {!client?.has_openai_key && (
        <div className="bg-amber-50 border border-amber-100 text-amber-700 px-4 py-3 rounded-lg text-sm flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
          OpenAI API key is not set —{" "}
          <a href="/settings" className="underline font-medium">configure in Settings</a>
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-base font-semibold text-slate-800 mb-1">Embed your bot</h2>
            <p className="text-slate-500 text-sm">
              Add the widget to any website with one snippet.
            </p>
          </div>
          <a
            href="/embed"
            className="shrink-0 px-3 py-1.5 text-sm font-medium text-violet-600 border border-violet-200 rounded-lg hover:bg-violet-50 transition-colors"
          >
            Configure →
          </a>
        </div>
        <CodeBlockWithCopy
          code={getEmbedSnippet()}
          copyLabel="Copy embed code"
          tone="light"
          preClassName="text-sm mb-3"
        />
        <p className="text-slate-400 text-xs">
          Choose between a floating chat bubble or an inline widget on the{" "}
          <a href="/embed" className="underline hover:text-slate-500">Embed page</a>.
        </p>
      </div>

    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    }>
      <DashboardContent />
    </Suspense>
  );
}
