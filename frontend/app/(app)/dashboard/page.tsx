"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { clearSession, api } from "@/lib/api";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";
import { buildEmbedSnippet } from "@/lib/widget-embed";
import { PageLoader } from "@/components/ui/page-loader";
import { useClientMe, useActiveBot } from "@/hooks/useApi";

function DashboardContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const showVerificationBanner = searchParams.get("verification_sent") === "1";

  const { data: client, error: clientError, isLoading: clientLoading } = useClientMe();
  const { activeBot: firstActiveBot, isLoading: botsLoading } = useActiveBot({ fallbackToFirst: true });

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
    return buildEmbedSnippet({ botId: botPublicId ?? "", configFormat: "inline" });
  }

  if (clientLoading || botsLoading) {
    return (
      <PageLoader />
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
      <PageLoader />
    }>
      <DashboardContent />
    </Suspense>
  );
}
