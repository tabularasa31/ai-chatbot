"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { clearSession, api } from "@/lib/api";
import { LoadError } from "@/components/ui/load-error";
import { InlineNotice } from "@/components/ui/inline-notice";
import { WidgetIdentityNotice } from "@/components/WidgetIdentityNotice";
import { useClientMe, useActiveBot } from "@/hooks/useApi";
import { InstallWidget } from "./install-widget";

function InstallWidgetSkeleton() {
  return (
    <div className="animate-pulse space-y-8" role="status" aria-label="Loading">
      <div className="h-11 w-2/3 rounded-control bg-surface-alt" />
      <div className="h-24 rounded-control bg-surface-alt" />
      <div className="h-28 rounded-control bg-surface-alt" />
      <div className="h-40 rounded-control bg-surface-alt" />
    </div>
  );
}

function isEmailNotVerified(error: unknown) {
  return error instanceof Error && error.message.toLowerCase().includes("email not verified");
}

function InstallWidgetContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const showVerificationNotice = searchParams.get("verification_sent") === "1";

  const { error: clientError, mutate: mutateClient } = useClientMe();
  const { activeBot, isLoading: botsLoading, error: botsError, mutate: mutateBots } = useActiveBot({
    fallbackToFirst: true,
  });

  useEffect(() => {
    if (!isEmailNotVerified(clientError)) return;
    clearSession();
    api.auth.logout();
    router.replace("/login?error=email_not_verified");
  }, [clientError, router]);

  function renderBody() {
    if (clientError && !isEmailNotVerified(clientError)) {
      return <LoadError message="Could not load your account." onRetry={() => mutateClient()} />;
    }
    if (botsError) {
      return <LoadError message="Could not load your bot." onRetry={() => mutateBots()} />;
    }
    if (botsLoading) return <InstallWidgetSkeleton />;
    if (!activeBot) {
      return <LoadError message="No bot found for this account." onRetry={() => mutateBots()} />;
    }
    return <InstallWidget botId={activeBot.public_id} />;
  }

  return (
    <div className="max-w-2xl space-y-8">
      {showVerificationNotice && (
        <InlineNotice>
          We sent a verification link to your email. Please check your inbox and click the link to verify your account.
        </InlineNotice>
      )}
      <WidgetIdentityNotice />
      <h1 className="text-h1 text-fg">Install widget</h1>
      {renderBody()}
    </div>
  );
}

export default function InstallWidgetPage() {
  return (
    <Suspense fallback={<InstallWidgetSkeleton />}>
      <InstallWidgetContent />
    </Suspense>
  );
}
