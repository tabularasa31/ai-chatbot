"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { clearSession, api } from "@/lib/api";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { InlineNotice } from "@/components/ui/inline-notice";
import { WidgetIdentityNotice } from "@/components/WidgetIdentityNotice";
import { useClientMe, useActiveBot } from "@/hooks/useApi";
import { InstallWidget } from "./install-widget";

function InstallWidgetSkeleton() {
  return (
    <SkeletonRegion>
      <div className="space-y-3">
        <Skeleton className="h-7 w-40" />
        <div className="flex items-center gap-3">
          <Skeleton className="h-10 w-72 max-w-full" />
          <Skeleton className="h-11 w-24" />
        </div>
      </div>
    </SkeletonRegion>
  );
}

function InstallWidgetPageSkeleton() {
  return (
    <div className="max-w-2xl space-y-8">
      <Skeleton className="h-8 w-48" />
      <InstallWidgetSkeleton />
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
      return <LoadError message="Couldn't load your account." onRetry={() => mutateClient()} />;
    }
    if (botsError) {
      return <LoadError message="Couldn't load your bot." onRetry={() => mutateBots()} />;
    }
    if (botsLoading) return <InstallWidgetSkeleton />;
    if (!activeBot) {
      return <LoadError message="No bot found for this account." onRetry={() => mutateBots()} />;
    }
    return <InstallWidget botId={activeBot.public_id} />;
  }

  return (
    <div className="max-w-2xl space-y-8">
      <h1 className="text-h1 text-fg">Install widget</h1>
      {showVerificationNotice && (
        <InlineNotice>
          We sent a verification link to your email. Please check your inbox and click the link to verify your account.
        </InlineNotice>
      )}
      <WidgetIdentityNotice />
      {renderBody()}
    </div>
  );
}

export default function InstallWidgetPage() {
  return (
    <Suspense fallback={<InstallWidgetPageSkeleton />}>
      <InstallWidgetContent />
    </Suspense>
  );
}
