"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { AuthCard, AuthFooter, AuthHeading, AuthSubmit, StandaloneLink } from "./AuthCard";
import { AuthField } from "./AuthField";
import { BrokenLink, type BrokenLinkProps } from "./BrokenLink";
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
import { Button } from "@/components/ui/button";
import { LoadError } from "@/components/ui/load-error";

const REDIRECT_DELAY_MS = 2000;
const CHECKING_REVEAL_DELAY_MS = 200;
const CHECK_FAILED_MESSAGE = "Check your connection and try again.";
const RATE_LIMITED_MESSAGE = "Too many attempts. Wait a while and try again.";
const NO_ERRORS: FieldErrors<"password" | "confirm"> = { password: null, confirm: null };

type View = "form" | "invalid" | "success";

type SetPasswordCopy = {
  heading: string;
  invalid: Omit<BrokenLinkProps, "centered">;
  passwordLabel: string;
  emptyPasswordMessage: string;
  submitLabel: string;
  loadingLabel: string;
  successTitle: string;
  footer: ReactNode;
};

type TokenCheck =
  | { status: "checking" }
  | { status: "valid" }
  | { status: "broken" }
  | { status: "failed"; message: string; retrying: boolean };

function CheckingLink() {
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setRevealed(true), CHECKING_REVEAL_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <AuthCard centered role="status" aria-busy="true">
      {revealed && (
        <>
          <AuthHeading>Checking your link…</AuthHeading>
          <Loader2 size={16} aria-hidden="true" className="mx-auto animate-spin text-fg-muted" />
        </>
      )}
    </AuthCard>
  );
}

function CheckFailed({ message, retrying, onRetry }: { message: string; retrying: boolean; onRetry: () => void }) {
  return (
    <AuthCard>
      <AuthHeading moveFocus>Couldn&apos;t check your link</AuthHeading>
      <p className="mb-6 text-body text-fg-muted">{message}</p>
      <Button variant="secondary" offset="surface" busy={retrying} className="w-full active:scale-press-wide" onClick={onRetry}>
        Try again
      </Button>
      <AuthFooter>
        <StandaloneLink href="/login">Sign in</StandaloneLink>
      </AuthFooter>
    </AuthCard>
  );
}

function useTokenCheck(token: string) {
  const [check, setCheck] = useState<TokenCheck>({ status: "checking" });

  const run = useCallback(async () => {
    try {
      await api.auth.checkResetToken(token);
      setCheck({ status: "valid" });
    } catch (err) {
      setCheck(
        isApiStatus(err, 400)
          ? { status: "broken" }
          : { status: "failed", message: isApiStatus(err, 429) ? RATE_LIMITED_MESSAGE : CHECK_FAILED_MESSAGE, retrying: false },
      );
    }
  }, [token]);

  useEffect(() => {
    void run();
  }, [run]);

  const retry = useCallback(() => {
    setCheck((current) => (current.status === "failed" ? { ...current, retrying: true } : current));
    void run();
  }, [run]);

  return { check, retry };
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

  if (view === "invalid") return <BrokenLink {...copy.invalid} />;

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

function CheckedPasswordEntry({ token, copy }: { token: string; copy: SetPasswordCopy }) {
  const { check, retry } = useTokenCheck(token);

  switch (check.status) {
    case "checking":
      return <CheckingLink />;
    case "broken":
      return <BrokenLink {...copy.invalid} />;
    case "failed":
      return <CheckFailed message={check.message} retrying={check.retrying} onRetry={retry} />;
    case "valid":
      return <PasswordEntry token={token} copy={copy} />;
  }
}

/**
 * Shared by /reset-password and /accept-invite: same token, same endpoints and
 * validation — only copy differs.
 */
export function SetPasswordForm(copy: SetPasswordCopy) {
  const token = useSearchParams().get("token");
  return token ? <CheckedPasswordEntry token={token} copy={copy} /> : <BrokenLink {...copy.invalid} />;
}
