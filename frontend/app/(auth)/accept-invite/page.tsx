"use client";

import { Suspense, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { AuthCard, AuthCardCentered, authStyles, validationHandlers } from "@/components/auth/AuthCard";

/**
 * Where an invite link lands. The token is the same one a password reset uses
 * — an invite and a reset are the same act, proving the address and setting a
 * password — so this page differs from /reset-password only in what it says.
 */
function AcceptInviteContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  if (!token) {
    return (
      <AuthCardCentered>
        <p className={`mb-4 ${authStyles.error}`}>Invalid invite link.</p>
        <p className="text-[#FAF5FF]/80 text-sm">
          Ask whoever invited you to send it again.
        </p>
      </AuthCardCentered>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      await api.auth.resetPassword(token, password);
      setSuccess(true);
      setTimeout(() => router.push("/login"), 2000);
    } catch (err) {
      const msg = (err as Error)?.message || "This invite link is invalid or has expired.";
      setError(typeof msg === "string" ? msg : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <AuthCardCentered>
        <h1 className={`${authStyles.headingSm} text-[#4ADE80]`}>You&apos;re in!</h1>
        <p className="text-[#FAF5FF]/80">Redirecting to sign in...</p>
      </AuthCardCentered>
    );
  }

  return (
    <AuthCard>
      <h1 className={authStyles.headingSm}>Join your team</h1>
      <p className={authStyles.subtext}>
        Choose a password to finish setting up your account (min 8 chars, 1 uppercase,
        1 number, 1 special character).
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="password" className={authStyles.label}>
            Password
          </label>
          <input
            id="password"
            type="password"
            placeholder="Min. 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onInvalid={validationHandlers.required.onInvalid}
            onInput={validationHandlers.required.onInput}
            required
            className={authStyles.input}
          />
        </div>

        <div>
          <label htmlFor="confirm" className={authStyles.label}>
            Confirm password
          </label>
          <input
            id="confirm"
            type="password"
            placeholder="Repeat password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            onInvalid={validationHandlers.required.onInvalid}
            onInput={validationHandlers.required.onInput}
            required
            className={authStyles.input}
          />
        </div>

        {error && <div className={authStyles.error}>{error}</div>}

        <button type="submit" disabled={loading} className={authStyles.button}>
          {loading ? "Setting up..." : "Accept invite"}
        </button>
      </form>

      <p className="mt-4 text-sm text-[#FAF5FF]/60">
        Already set a password?{" "}
        <Link href="/login" className={`font-medium ${authStyles.link}`}>
          Sign in
        </Link>
      </p>
    </AuthCard>
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
