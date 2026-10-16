"use client";

import { useState } from "react";
import { flushSync } from "react-dom";
import { api } from "@/lib/api";
import { AuthCard, AuthFooter, AuthHeading, AuthLink, AuthSubmit, StandaloneLink } from "@/components/auth/AuthCard";
import { AuthField, EMAIL_INPUT_PROPS } from "@/components/auth/AuthField";
import {
  PASSWORD_HELPER,
  PASSWORD_RULE_ERROR,
  applyFieldErrors,
  emailError,
  focusField,
  formErrorMessage,
  hasErrors,
  isApiStatus,
  newPasswordError,
  type FieldErrors,
} from "@/components/auth/auth-rules";
import { Button } from "@/components/ui/button";
import { LoadError } from "@/components/ui/load-error";

type FormProblem = { kind: "duplicate" } | { kind: "message"; message: string } | null;

const NO_ERRORS: FieldErrors<"email" | "password"> = { email: null, password: null };

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState(NO_ERRORS);
  const [formError, setFormError] = useState<FormProblem>(null);
  const [submitting, setSubmitting] = useState(false);
  const [verificationSent, setVerificationSent] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);
    const next = { email: emailError(email), password: newPasswordError(password, "Enter a password.") };
    applyFieldErrors(setErrors, next);
    if (hasErrors(next)) return;

    setSubmitting(true);
    try {
      await api.auth.register(email, password);
      setVerificationSent(true);
    } catch (err) {
      if (isApiStatus(err, 409)) {
        setFormError({ kind: "duplicate" });
      } else if (isApiStatus(err, 422)) {
        applyFieldErrors(setErrors, { email: null, password: PASSWORD_RULE_ERROR });
      } else {
        setFormError({ kind: "message", message: formErrorMessage(err) });
      }
    } finally {
      setSubmitting(false);
    }
  }

  function editEmail() {
    setPassword("");
    flushSync(() => setVerificationSent(false));
    focusField("email");
  }

  if (verificationSent) {
    return (
      <AuthCard centered>
        <AuthHeading moveFocus>Check your email</AuthHeading>
        <p className="mb-4 text-body text-fg-muted">
          We sent a verification link to <span className="break-all font-medium text-fg">{email}</span>. Open it to
          activate your account.
        </p>
        <p className="mb-6 text-ui text-fg-muted">Can&apos;t find it? Check your spam folder.</p>
        <Button variant="secondary" offset="surface" className="w-full" onClick={editEmail}>
          Use a different email
        </Button>
        <AuthFooter className="justify-center">
          <StandaloneLink href="/login">Back to sign in</StandaloneLink>
        </AuthFooter>
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <AuthHeading>Create account</AuthHeading>
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <AuthField
          id="email"
          label="Email"
          {...EMAIL_INPUT_PROPS}
          autoComplete="email"
          required
          readOnly={submitting}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setErrors((prev) => ({ ...prev, email: null }));
          }}
          error={errors.email}
        />
        <AuthField
          id="password"
          label="Password"
          type="password"
          autoComplete="new-password"
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
        {formError && (
          <LoadError
            message={
              formError.kind === "duplicate" ? (
                <>
                  An account with this email already exists. <AuthLink href="/login">Sign in</AuthLink>
                </>
              ) : (
                formError.message
              )
            }
          />
        )}
        <AuthSubmit submitting={submitting} label="Create account" loadingLabel="Creating account…" />
      </form>
      <AuthFooter>
        Already have an account?
        <StandaloneLink href="/login">Sign in</StandaloneLink>
      </AuthFooter>
    </AuthCard>
  );
}
