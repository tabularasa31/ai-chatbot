"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api, getToken, saveToken } from "@/lib/api";
import { AuthCard, authStyles, validationHandlers } from "@/components/auth/AuthCard";
import { AuthTransition } from "@/components/AuthTransition";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [transitioning, setTransitioning] = useState(false);

  const notVerified = searchParams.get("error") === "email_not_verified";
  const sessionExpired = searchParams.get("error") === "session_expired";

  const onAuthTransitionComplete = useCallback(() => {
    router.replace("/dashboard");
  }, [router]);

  useEffect(() => {
    const token = getToken();
    if (token) {
      saveToken(token);
      router.replace("/dashboard");
    }
  }, [router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { token } = await api.auth.login(email, password);
      saveToken(token);
      setTransitioning(true);
    } catch (err) {
      const msg = (err as Error)?.message || (err as { detail?: string })?.detail || "An error occurred";
      setError(typeof msg === "string" ? msg : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard>
      {transitioning && <AuthTransition onComplete={onAuthTransitionComplete} />}
      <h1 className={authStyles.heading}>Sign in</h1>
      {sessionExpired && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-200 text-sm rounded-lg px-4 py-3 mb-2">
          Your session expired. Please sign in again.
        </div>
      )}
      {notVerified && (
        <div className="bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm rounded-lg px-4 py-3 mb-2">
          Please verify your email before signing in. Check your inbox for the verification link.
        </div>
      )}
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
          <div className="text-right mt-1">
            <Link href="/forgot-password" className={`text-sm ${authStyles.link}`}>
              Forgot password?
            </Link>
          </div>
        </div>
        {error && <div className={authStyles.error}>{error}</div>}
        <button type="submit" disabled={loading} className={authStyles.button}>
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <p className={authStyles.footer}>
        Don&apos;t have an account?{" "}
        <Link href="/signup" className={authStyles.link}>
          Sign up
        </Link>
      </p>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
