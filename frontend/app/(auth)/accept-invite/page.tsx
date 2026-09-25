"use client";

import { Suspense } from "react";
import Link from "next/link";
import { AuthCardCentered, authStyles } from "@/components/auth/AuthCard";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

/**
 * Where an invite link lands. The token is the same one a password reset uses
 * — an invite and a reset are the same act, proving the address and setting a
 * password — so this page differs from /reset-password only in what it says.
 */
function AcceptInviteContent() {
  return (
    <SetPasswordForm
      invalidTokenTitle="Invalid invite link."
      invalidTokenBody={
        <p className="text-[#FAF5FF]/80 text-sm">
          Ask whoever invited you to send it again.
        </p>
      }
      heading="Join your team"
      subtext="Choose a password to finish setting up your account (min 8 chars, 1 uppercase, 1 number, 1 special character)."
      passwordLabel="Password"
      submitLabel="Accept invite"
      loadingLabel="Setting up..."
      successTitle="You're in!"
      fallbackError="This invite link is invalid or has expired."
      footer={
        <p className="mt-4 text-sm text-[#FAF5FF]/60">
          Already set a password?{" "}
          <Link href="/login" className={`font-medium ${authStyles.link}`}>
            Sign in
          </Link>
        </p>
      }
    />
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <AuthCardCentered>
          <div className="animate-pulse text-[#FAF5FF]/60">Loading...</div>
        </AuthCardCentered>
      }
    >
      <AcceptInviteContent />
    </Suspense>
  );
}
