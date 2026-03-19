"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { AuthCard, AuthCardCentered, authStyles } from "@/components/auth/AuthCard";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      await api.auth.forgotPassword(email);
      setSubmitted(true);
    } catch (err) {
      const msg = (err as Error)?.message || "Something went wrong";
      setError(typeof msg === "string" ? msg : "Failed to send reset link");
    } finally {
      setLoading(false);
    }
  };

  if (submitted) {
    return (
      <AuthCardCentered>
        <h1 className={authStyles.headingSm}>Check your email</h1>
        <p className="text-[#FAF5FF]/80 mb-6">
          If this email is registered, you&apos;ll receive a password reset link shortly.
        </p>
        <Link href="/login" className={`font-medium ${authStyles.link}`}>
          Back to sign in
        </Link>
      </AuthCardCentered>
    );
  }

  return (
    <AuthCard>
      <h1 className={authStyles.headingSm}>Reset password</h1>
      <p className={authStyles.subtext}>Enter your email and we&apos;ll send you a reset link.</p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="email" className={authStyles.label}>
            Email
          </label>
          <input
            id="email"
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className={authStyles.input}
          />
        </div>

        {error && <div className={authStyles.error}>{error}</div>}

        <button type="submit" disabled={loading} className={authStyles.button}>
          {loading ? "Sending..." : "Send reset link"}
        </button>
      </form>

      <p className={authStyles.footer}>
        <Link href="/login" className={authStyles.link}>
          Back to sign in
        </Link>
      </p>
    </AuthCard>
  );
}
