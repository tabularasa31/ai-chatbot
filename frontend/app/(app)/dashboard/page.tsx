"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { clearSession, api } from "@/lib/api";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";
import { buildEmbedSnippet } from "@/lib/widget-embed";
import { PageLoader } from "@/components/ui/page-loader";
import { LoadError } from "@/components/ui/load-error";
import { InlineNotice } from "@/components/ui/inline-notice";
import { WidgetIdentityNotice } from "@/components/WidgetIdentityNotice";
import { useClientMe, useActiveBot } from "@/hooks/useApi";

function DashboardContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const showVerificationBanner = searchParams.get("verification_sent") === "1";

  const { error: clientError, isLoading: clientLoading, mutate: mutateClient } = useClientMe();
  const { activeBot: firstActiveBot, isLoading: botsLoading, error: botsError, mutate: mutateBots } = useActiveBot({ fallbackToFirst: true });

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

  if (clientLoading || (botsLoading && !botsError)) {
    return (
      <PageLoader />
    );
  }

  if (clientError && !(clientError instanceof Error && clientError.message.toLowerCase().includes("email not verified"))) {
    return (
      <LoadError message="Couldn't load your account. Try again." onRetry={() => mutateClient()} />
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {showVerificationBanner && (
        <InlineNotice>
          We sent a verification link to your email. Please check your inbox and click the link to verify your account.
        </InlineNotice>
      )}
      <WidgetIdentityNotice />
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Dashboard</h1>
      </div>

      {botPublicId && (
        <div className="bg-white rounded-xl border border-slate-200 p-6">
          <div className="mb-6">
            <h2 className="text-base font-semibold text-slate-800 mb-1">Your Bot ID</h2>
            <p className="mb-2 text-sm text-slate-500">
              Public bot identifier used in the widget snippet.
            </p>
            <code className="flex-1 min-w-0 px-3 py-2 bg-slate-100 rounded-lg text-sm text-slate-800 break-all font-mono">
              {botPublicId}
            </code>
          </div>
        </div>
      )}

      {botPublicId ? (
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
          code={buildEmbedSnippet({ botId: botPublicId, configFormat: "inline" })}
          copyLabel="Copy embed code"
          tone="light"
          preClassName="text-sm mb-3"
        />
        <p className="text-slate-400 text-xs">
          Choose between a floating chat bubble or an inline widget on the{" "}
          <a href="/embed" className="underline hover:text-slate-500">Embed page</a>.
        </p>
      </div>
      ) : botsError ? (
        <LoadError message="Couldn't load your bot. Try again." onRetry={() => mutateBots()} />
      ) : null}

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
