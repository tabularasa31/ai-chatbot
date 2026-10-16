"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { api, markSession } from "@/lib/api";
import {
  AuthCard,
  AuthHeading,
  AuthSkeleton,
  StandaloneLink,
} from "@/components/auth/AuthCard";
import { fullWidthLinkButtonClasses } from "@/components/auth/link-classes";
import { formErrorMessage, isApiStatus } from "@/components/auth/auth-rules";
import { Button } from "@/components/ui/button";

type VerifyState =
  | { status: "verifying"; retried: boolean }
  | { status: "success" }
  | { status: "rejected" }
  | { status: "failed"; message: string };

function BrokenLink({ explanation }: { explanation: string }) {
  return (
    <AuthCard centered>
      <AuthHeading moveFocus>This link doesn&apos;t work</AuthHeading>
      <p className="mb-6 text-body text-fg-muted">{explanation}</p>
      <Link href="/login" className={fullWidthLinkButtonClasses}>
        Sign in
      </Link>
    </AuthCard>
  );
}

function TokenVerification({ token }: { token: string }) {
  const [state, setState] = useState<VerifyState>({ status: "verifying", retried: false });

  const verify = useCallback(async (retried: boolean) => {
    setState({ status: "verifying", retried });
    try {
      await api.auth.verifyEmail(token);
      markSession();
      setState({ status: "success" });
      window.location.replace("/dashboard");
    } catch (err) {
      setState(isApiStatus(err, 400) ? { status: "rejected" } : { status: "failed", message: formErrorMessage(err) });
    }
  }, [token]);

  useEffect(() => {
    void verify(false);
  }, [verify]);

  if (state.status === "verifying") {
    return (
      <AuthCard centered role="status">
        <AuthHeading moveFocus={state.retried}>Verifying your email…</AuthHeading>
        <Loader2 size={16} aria-busy="true" aria-hidden="true" className="mx-auto animate-spin text-fg-muted" />
      </AuthCard>
    );
  }

  if (state.status === "success") {
    return (
      <AuthCard centered>
        <AuthHeading moveFocus>Email verified</AuthHeading>
        <p className="text-body text-fg-muted">Taking you to your dashboard…</p>
      </AuthCard>
    );
  }

  if (state.status === "rejected") {
    return (
      <BrokenLink explanation="It may have expired or already been used. If you already verified your email, sign in." />
    );
  }

  return (
    <AuthCard centered>
      <AuthHeading moveFocus>Couldn&apos;t verify your email</AuthHeading>
      <p className="mb-6 text-body text-fg-muted">{state.message}</p>
      <Button variant="secondary" offset="surface" className="w-full" onClick={() => verify(true)}>
        Try again
      </Button>
      <div className="mt-4 flex justify-center">
        <StandaloneLink href="/login">Sign in</StandaloneLink>
      </div>
    </AuthCard>
  );
}

function VerifyContent() {
  const token = useSearchParams().get("token");
  return token ? (
    <TokenVerification token={token} />
  ) : (
    <BrokenLink explanation="This link is incomplete. Open it again from the email." />
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={<AuthSkeleton />}>
      <VerifyContent />
    </Suspense>
  );
}
