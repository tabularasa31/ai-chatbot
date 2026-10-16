"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { AuthCard, AuthHeading, AuthSubmit, fullWidthLinkButtonClasses } from "./AuthCard";
import { AuthField } from "./AuthField";
import {
  PASSWORD_HELPER,
  PASSWORD_MAX_LENGTH,
  PASSWORD_RULE_ERROR,
  focusFirstError,
  formErrorMessage,
  hasErrors,
  isApiStatus,
  newPasswordError,
  type FieldErrors,
} from "./auth-rules";
import { LoadError } from "@/components/ui/load-error";

const REDIRECT_DELAY_MS = 2000;
const NO_ERRORS: FieldErrors<"password" | "confirm"> = { password: null, confirm: null };

type View = "form" | "invalid" | "success";

/**
 * Shared by /reset-password and /accept-invite: same token, same endpoint and
 * validation — only copy differs.
 */
export function SetPasswordForm({
  heading,
  invalidTitle,
  invalidBody,
  passwordLabel,
  emptyPasswordMessage,
  submitLabel,
  loadingLabel,
  successTitle,
  footer,
}: {
  heading: string;
  invalidTitle: string;
  invalidBody: string;
  passwordLabel: string;
  emptyPasswordMessage: string;
  submitLabel: string;
  loadingLabel: string;
  successTitle: string;
  footer: ReactNode;
}) {
  const token = useSearchParams().get("token");
  const router = useRouter();

  const [view, setView] = useState<View>(token ? "form" : "invalid");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState(NO_ERRORS);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [swapped, setSwapped] = useState(false);

  useEffect(() => {
    if (view !== "success") return;
    const timer = setTimeout(() => router.push("/login"), REDIRECT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [view, router]);

  function showView(next: View) {
    setView(next);
    setSwapped(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !token) return;
    setFormError(null);
    const passwordProblem = newPasswordError(password, emptyPasswordMessage);
    const next = {
      password: passwordProblem,
      confirm: !confirm
        ? "Repeat your new password."
        : !passwordProblem && confirm !== password
          ? "Passwords don't match."
          : null,
    };
    setErrors(next);
    if (hasErrors(next)) {
      focusFirstError(next);
      return;
    }

    setSubmitting(true);
    try {
      await api.auth.resetPassword(token, password);
      showView("success");
    } catch (err) {
      if (isApiStatus(err, 400)) {
        showView("invalid");
      } else if (isApiStatus(err, 422)) {
        setErrors({ password: PASSWORD_RULE_ERROR, confirm: null });
        focusFirstError({ password: PASSWORD_RULE_ERROR });
      } else {
        setFormError(formErrorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (view === "success") {
    return (
      <AuthCard centered>
        <AuthHeading moveFocus>{successTitle}</AuthHeading>
        <p className="mb-6 text-body text-fg-muted">Taking you to sign in…</p>
        <Link href="/login" className={fullWidthLinkButtonClasses}>
          Sign in
        </Link>
      </AuthCard>
    );
  }

  if (view === "invalid") {
    return (
      <AuthCard>
        <AuthHeading moveFocus={swapped}>{invalidTitle}</AuthHeading>
        <p className="mb-6 text-body text-fg-muted">{invalidBody}</p>
        <Link href="/forgot-password" className={fullWidthLinkButtonClasses}>
          Request a new link
        </Link>
        {footer}
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <AuthHeading>{heading}</AuthHeading>
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <AuthField
          id="password"
          label={passwordLabel}
          type="password"
          autoComplete="new-password"
          maxLength={PASSWORD_MAX_LENGTH}
          required
          readOnly={submitting}
          value={password}
          helper={PASSWORD_HELPER}
          onChange={(e) => {
            setPassword(e.target.value);
            setErrors((prev) => ({ ...prev, password: null }));
          }}
          error={errors.password}
        />
        <AuthField
          id="confirm"
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          maxLength={PASSWORD_MAX_LENGTH}
          required
          readOnly={submitting}
          value={confirm}
          onChange={(e) => {
            setConfirm(e.target.value);
            setErrors((prev) => ({ ...prev, confirm: null }));
          }}
          error={errors.confirm}
        />
        {formError && <LoadError message={formError} />}
        <AuthSubmit submitting={submitting} label={submitLabel} loadingLabel={loadingLabel} />
      </form>
      {footer}
    </AuthCard>
  );
}
