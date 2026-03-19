"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, getToken, saveToken } from "@/lib/api";
import { AuthCard, authStyles } from "@/components/auth/AuthCard";

export default function LoginPage() {
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
      const { token } = await api.auth.login(email, password);
      saveToken(token);
      router.replace("/dashboard");
    } catch (err) {
      const msg = (err as Error)?.message || (err as { detail?: string })?.detail || "An error occurred";
      setError(typeof msg === "string" ? msg : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard>
      <h1 className={authStyles.heading}>Sign in</h1>
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
