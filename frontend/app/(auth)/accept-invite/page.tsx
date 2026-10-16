import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthFooter, AuthLink, AuthSkeleton, standaloneLinkClasses } from "@/components/auth/AuthCard";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

export const metadata: Metadata = { title: "Join your team - Chat9" };

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<AuthSkeleton />}>
      <SetPasswordForm
        heading="Join your team"
        invalidTitle="This invite link doesn't work"
        invalidBody="It may have expired or already been used. Ask whoever invited you to send it again, or request a new link."
        passwordLabel="Password"
        emptyPasswordMessage="Enter a password."
        submitLabel="Accept invite"
        loadingLabel="Setting up…"
        successTitle="Account ready"
        footer={
          <AuthFooter>
            Already set a password?
            <AuthLink href="/login" className={standaloneLinkClasses}>
              Sign in
            </AuthLink>
          </AuthFooter>
        }
      />
    </Suspense>
  );
}
