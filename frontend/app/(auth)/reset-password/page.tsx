import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthFooter, AuthLink, AuthSkeleton, standaloneLinkClasses } from "@/components/auth/AuthCard";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

export const metadata: Metadata = { title: "Set a new password - Chat9" };

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<AuthSkeleton />}>
      <SetPasswordForm
        heading="Choose a new password"
        invalidTitle="This link doesn't work"
        invalidBody="It may have expired or already been used. Request a new one."
        passwordLabel="New password"
        emptyPasswordMessage="Enter a new password."
        submitLabel="Save new password"
        loadingLabel="Saving…"
        successTitle="Password updated"
        footer={
          <AuthFooter>
            <AuthLink href="/login" className={standaloneLinkClasses}>
              Back to sign in
            </AuthLink>
          </AuthFooter>
        }
      />
    </Suspense>
  );
}
