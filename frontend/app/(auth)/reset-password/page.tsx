"use client";

import { Suspense } from "react";
import Link from "next/link";
import { AuthCardCentered, authStyles } from "@/components/auth/AuthCard";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

function ResetPasswordContent() {
  return (
    <SetPasswordForm
      invalidTokenTitle="Invalid reset link."
      invalidTokenBody={
        <Link href="/forgot-password" className={`font-medium ${authStyles.link}`}>
          Request a new one
        </Link>
      }
      heading="Set new password"
      subtext="Choose a strong password (min 8 chars, 1 uppercase, 1 number, 1 special character)."
      passwordLabel="New password"
      submitLabel="Reset password"
      loadingLabel="Updating..."
      successTitle="Password updated!"
      fallbackError="Invalid or expired link. Please request a new one."
    />
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <AuthCardCentered>
          <div className="animate-pulse text-[#FAF5FF]/60">Loading...</div>
        </AuthCardCentered>
      }
    >
      <ResetPasswordContent />
    </Suspense>
  );
}
