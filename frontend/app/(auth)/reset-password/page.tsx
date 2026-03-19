"use client";

import { Suspense, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { AuthCard, AuthCardCentered, authStyles } from "@/components/auth/AuthCard";

function ResetPasswordContent() {
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
        <p className={`mb-4 ${authStyles.error}`}>Invalid reset link.</p>
        <Link href="/forgot-password" className={`font-medium ${authStyles.link}`}>
          Request a new one
        </Link>
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
      const msg = (err as Error)?.message || "Invalid or expired link. Please request a new one.";
      setError(typeof msg === "string" ? msg : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <AuthCardCentered>
        <h1 className={`${authStyles.headingSm} text-[#4ADE80]`}>Password updated!</h1>
        <p className="text-[#FAF5FF]/80">Redirecting to sign in...</p>
      </AuthCardCentered>
    );
  }

  return (
    <AuthCard>
      <h1 className={authStyles.headingSm}>Set new password</h1>
      <p className={authStyles.subtext}>
        Choose a strong password (min 8 chars, 1 uppercase, 1 number, 1 special character).
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="password" className={authStyles.label}>
            New password
          </label>
          <input
            id="password"
            type="password"
            placeholder="Min. 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
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
            required
            className={authStyles.input}
          />
        </div>

        {error && <div className={authStyles.error}>{error}</div>}

        <button type="submit" disabled={loading} className={authStyles.button}>
          {loading ? "Updating..." : "Reset password"}
        </button>
      </form>
    </AuthCard>
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
