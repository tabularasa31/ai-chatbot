"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, getToken } from "@/lib/api";
import { AuthCard, AuthCardCentered, authStyles, validationHandlers } from "@/components/auth/AuthCard";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [verificationSent, setVerificationSent] = useState(false);

  useEffect(() => {
    if (getToken()) {
      router.replace("/dashboard");
    }
  }, [router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.auth.register(email, password);
      setVerificationSent(true);
    } catch (err) {
      const msg = (err as Error)?.message || (err as { detail?: string })?.detail || "An error occurred";
      setError(typeof msg === "string" ? msg : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  if (verificationSent) {
    return (
      <AuthCardCentered>
        <div className="flex flex-col items-center gap-4">
          <div className="w-14 h-14 rounded-full bg-[#E879F9]/10 flex items-center justify-center">
            <svg className="w-7 h-7 text-[#E879F9]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25H4.5a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5H4.5a2.25 2.25 0 00-2.25 2.25m19.5 0-9.75 6.75L2.25 6.75" />
            </svg>
          </div>
          <h1 className={authStyles.headingSm}>Check your inbox</h1>
          <p className={authStyles.subtext}>
            We sent a verification link to{" "}
            <span className="text-[#E879F9] font-medium">{email}</span>.
            Click the link to activate your account.
          </p>
          <p className="text-[#FAF5FF]/40 text-xs">
            Didn&apos;t get the email? Check your spam folder.
          </p>
        </div>
      </AuthCardCentered>
    );
  }

  return (
    <AuthCard>
      <h1 className={authStyles.heading}>Create account</h1>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="email" className={authStyles.label}>
            Email
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onInvalid={validationHandlers.email.onInvalid}
            onInput={validationHandlers.email.onInput}
            required
            className={authStyles.input}
            placeholder="you@example.com"
          />
        </div>
        <div>
          <label htmlFor="password" className={authStyles.label}>
            Password
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onInvalid={validationHandlers.required.onInvalid}
            onInput={validationHandlers.required.onInput}
            required
            className={authStyles.input}
          />
        </div>
        {error && <div className={authStyles.error}>{error}</div>}
        <button type="submit" disabled={loading} className={authStyles.button}>
          {loading ? "Creating account..." : "Sign up"}
        </button>
      </form>
      <p className={authStyles.footer}>
        Already have an account?{" "}
        <Link href="/login" className={authStyles.link}>
          Sign in
        </Link>
      </p>
    </AuthCard>
  );
}
