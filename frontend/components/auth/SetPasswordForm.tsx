"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { AuthCard, AuthHeading, AuthSubmit } from "./AuthCard";
import { AuthField } from "./AuthField";
import {
  PASSWORD_HELPER,
  PASSWORD_MAX_LENGTH,
  PASSWORD_RULE_ERROR,
  applyFieldErrors,
  formErrorMessage,
  hasErrors,
  isApiStatus,
  newPasswordError,
  type FieldErrors,
} from "./auth-rules";
import { fullWidthLinkButtonClasses } from "./link-classes";
import { LoadError } from "@/components/ui/load-error";

const REDIRECT_DELAY_MS = 2000;
const NO_ERRORS: FieldErrors<"password" | "confirm"> = { password: null, confirm: null };

type View = "form" | "invalid" | "success";

type SetPasswordCopy = {
  heading: string;
  invalidTitle: string;
  invalidBody: string;
  passwordLabel: string;
  emptyPasswordMessage: string;
  submitLabel: string;
  loadingLabel: string;
  successTitle: string;
  footer: ReactNode;
};

function InvalidLink({
  copy,
  moveFocus,
}: {
  copy: Pick<SetPasswordCopy, "invalidTitle" | "invalidBody" | "footer">;
  moveFocus: boolean;
}) {
  return (
    <AuthCard>
      <AuthHeading moveFocus={moveFocus}>{copy.invalidTitle}</AuthHeading>
      <p className="mb-6 text-body text-fg-muted">{copy.invalidBody}</p>
      <Link href="/forgot-password" className={fullWidthLinkButtonClasses}>
        Request a new link
      </Link>
      {copy.footer}
    </AuthCard>
  );
}

function PasswordEntry({ token, copy }: { token: string; copy: SetPasswordCopy }) {
  const router = useRouter();
  const [view, setView] = useState<View>("form");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState(NO_ERRORS);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (view !== "success") return;
    const timer = setTimeout(() => router.push("/login"), REDIRECT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [view, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);
    const passwordProblem = newPasswordError(password, copy.emptyPasswordMessage);
    const next = {
      password: passwordProblem,
      confirm: !confirm
        ? "Repeat your new password."
        : !passwordProblem && confirm !== password
          ? "Passwords don't match."
          : null,
    };
    applyFieldErrors(setErrors, next);
    if (hasErrors(next)) return;

    setSubmitting(true);
    try {
      await api.auth.resetPassword(token, password);
      setView("success");
    } catch (err) {
      if (isApiStatus(err, 400)) {
        setView("invalid");
      } else if (isApiStatus(err, 422)) {
        applyFieldErrors(setErrors, { password: PASSWORD_RULE_ERROR, confirm: null });
      } else {
        setFormError(formErrorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (view === "success") {
    return (
      <AuthCard>
        <AuthHeading moveFocus>{copy.successTitle}</AuthHeading>
        <p className="mb-6 text-body text-fg-muted">Taking you to sign in…</p>
        <Link href="/login" className={fullWidthLinkButtonClasses}>
          Sign in
        </Link>
      </AuthCard>
    );
  }

  if (view === "invalid") return <InvalidLink copy={copy} moveFocus />;

  return (
    <AuthCard>
      <AuthHeading>{copy.heading}</AuthHeading>
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <AuthField
          id="password"
          label={copy.passwordLabel}
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
        <AuthSubmit submitting={submitting} label={copy.submitLabel} loadingLabel={copy.loadingLabel} />
      </form>
      {copy.footer}
    </AuthCard>
  );
}

/**
 * Shared by /reset-password and /accept-invite: same token, same endpoint and
 * validation — only copy differs.
 */
export function SetPasswordForm(copy: SetPasswordCopy) {
  const token = useSearchParams().get("token");
  return token ? <PasswordEntry token={token} copy={copy} /> : <InvalidLink copy={copy} moveFocus={false} />;
}
