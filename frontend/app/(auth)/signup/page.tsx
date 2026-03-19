"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, getToken, saveToken } from "@/lib/api";
import { AuthCard, authStyles, validationHandlers } from "@/components/auth/AuthCard";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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
      const { token } = await api.auth.register(email, password);
      saveToken(token);
      router.replace("/dashboard?verification_sent=1");
    } catch (err) {
      const msg = (err as Error)?.message || (err as { detail?: string })?.detail || "An error occurred";
      setError(typeof msg === "string" ? msg : "Registration failed");
    } finally {
      setLoading(false);
    }
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
