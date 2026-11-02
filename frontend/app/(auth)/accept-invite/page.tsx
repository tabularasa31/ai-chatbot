import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthFooter, AuthSkeleton, StandaloneLink } from "@/components/auth/AuthCard";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

export const metadata: Metadata = { title: "Join your team" };

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<AuthSkeleton />}>
      <SetPasswordForm
        heading="Join your team"
        invalid={{
          title: "This invite link doesn't work",
          explanation: "It may have expired or already been used. If you already joined, sign in. Otherwise ask whoever invited you to send a new invite.",
          action: { href: "/login", label: "Sign in" },
        }}
        passwordLabel="Password"
        emptyPasswordMessage="Enter a password."
        submitLabel="Accept invite"
        loadingLabel="Setting up…"
        successTitle="Account ready"
        footer={
          <AuthFooter>
            Already set a password?
            <StandaloneLink href="/login">Sign in</StandaloneLink>
          </AuthFooter>
        }
      />
    </Suspense>
  );
}
