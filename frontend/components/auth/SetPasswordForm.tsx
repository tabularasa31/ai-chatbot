"use client";

import { useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { AuthCard, AuthCardCentered, authStyles, validationHandlers } from "./AuthCard";

/**
 * Shared by /reset-password and /accept-invite: same token, same validation,
 * same API call and redirect — only copy (title, labels, fallback error) differs.
 */
export function SetPasswordForm({
  invalidTokenTitle,
  invalidTokenBody,
  heading,
  subtext,
  passwordLabel,
  submitLabel,
  loadingLabel,
  successTitle,
  fallbackError,
  footer,
}: {
  invalidTokenTitle: string;
  invalidTokenBody: ReactNode;
  heading: string;
  subtext: ReactNode;
  passwordLabel: string;
  submitLabel: string;
  loadingLabel: string;
  successTitle: string;
  fallbackError: string;
  footer?: ReactNode;
}) {
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
        <p className={`mb-4 ${authStyles.error}`}>{invalidTokenTitle}</p>
        {invalidTokenBody}
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
      const msg = (err as Error)?.message || fallbackError;
      setError(typeof msg === "string" ? msg : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <AuthCardCentered>
        <h1 className={`${authStyles.headingSm} text-[#4ADE80]`}>{successTitle}</h1>
        <p className="text-[#FAF5FF]/80">Redirecting to sign in...</p>
      </AuthCardCentered>
    );
  }

  return (
    <AuthCard>
      <h1 className={authStyles.headingSm}>{heading}</h1>
      <p className={authStyles.subtext}>{subtext}</p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="password" className={authStyles.label}>
            {passwordLabel}
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
          {loading ? loadingLabel : submitLabel}
        </button>
      </form>

      {footer}
    </AuthCard>
  );
}
