import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthFooter, AuthSkeleton, StandaloneLink } from "@/components/auth/AuthCard";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

export const metadata: Metadata = { title: "Choose a new password - Chat9" };

const backToSignIn = (
  <AuthFooter>
    <StandaloneLink href="/login">Back to sign in</StandaloneLink>
  </AuthFooter>
);

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<AuthSkeleton />}>
      <SetPasswordForm
        heading="Choose a new password"
        invalid={{
          title: "This link doesn't work",
          explanation: "It may have expired or already been used. Request a new one.",
          action: { href: "/forgot-password", label: "Request a new link" },
          children: backToSignIn,
        }}
        passwordLabel="New password"
        emptyPasswordMessage="Enter a new password."
        submitLabel="Save new password"
        loadingLabel="Saving…"
        successTitle="Password updated"
        footer={backToSignIn}
      />
    </Suspense>
  );
}
