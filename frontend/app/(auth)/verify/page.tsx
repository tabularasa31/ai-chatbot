"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { AuthCardCentered, authStyles } from "@/components/auth/AuthCard";

type Status = "idle" | "loading" | "success" | "error";

function VerifyContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setError("No verification token provided.");
      return;
    }
    setStatus("loading");
    api.auth
      .verifyEmail(token)
      .then(() => setStatus("success"))
      .catch((err) => {
        setStatus("error");
        setError(err instanceof Error ? err.message : "Verification failed");
      });
  }, [token]);

  if (status === "idle" || status === "loading") {
    return (
      <AuthCardCentered>
        <h1 className={authStyles.headingSm}>Verify your email</h1>
        <p className="text-[#FAF5FF]/80 text-sm mb-4">Confirming your link…</p>
        <div className="animate-pulse text-[#FAF5FF]/60 text-sm">Please wait</div>
      </AuthCardCentered>
    );
  }

  if (status === "success") {
    return (
      <AuthCardCentered>
        <h1 className={`${authStyles.headingSm} text-[#4ADE80]`}>Email verified successfully</h1>
        <p className="text-[#FAF5FF]/80 mb-6">
          Your email has been verified. You can now sign in to your account.
        </p>
        <Link href="/login" className={authStyles.ctaLink}>
          Go to Sign in
        </Link>
      </AuthCardCentered>
    );
  }

  return (
    <AuthCardCentered>
      <h1 className={`${authStyles.headingSm} text-[#F87171]`}>Verification failed</h1>
      <p className="text-[#FAF5FF]/80 mb-6">{error}</p>
      <Link href="/login" className={authStyles.ctaLink}>
        Go to Sign in
      </Link>
    </AuthCardCentered>
  );
}

export default function VerifyPage() {
  return (
    <Suspense
      fallback={
        <AuthCardCentered>
          <div className="animate-pulse text-[#FAF5FF]/60">Loading...</div>
        </AuthCardCentered>
      }
    >
      <VerifyContent />
    </Suspense>
  );
}
