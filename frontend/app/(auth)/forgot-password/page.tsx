"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import {
  AuthCard,
  AuthFooter,
  AuthHeading,
  AuthLink,
  AuthSubmit,
  standaloneLinkClasses,
} from "@/components/auth/AuthCard";
import { AuthField, EMAIL_INPUT_PROPS } from "@/components/auth/AuthField";
import { emailError, focusFirstError, formErrorMessage } from "@/components/auth/auth-rules";
import { Button } from "@/components/ui/button";
import { LoadError } from "@/components/ui/load-error";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);
    const invalid = emailError(email);
    setFieldError(invalid);
    if (invalid) {
      focusFirstError({ email: invalid });
      return;
    }

    setSubmitting(true);
    try {
      await api.auth.forgotPassword(email);
      setSubmitted(true);
    } catch (err) {
      setFormError(formErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <AuthCard centered>
        <AuthHeading moveFocus>Check your email</AuthHeading>
        <p className="mb-6 text-body text-fg-muted">
          If an account exists for <span className="break-all font-medium text-fg">{email}</span>, we sent a link to
          reset the password.
        </p>
        <Button variant="secondary" offset="surface" className="w-full" onClick={() => setSubmitted(false)}>
          Use a different email
        </Button>
        <AuthFooter className="justify-center">
          <AuthLink href="/login" className={standaloneLinkClasses}>
            Back to sign in
          </AuthLink>
        </AuthFooter>
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <AuthHeading>Reset your password</AuthHeading>
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
            setFieldError(null);
          }}
          error={fieldError}
        />
        {formError && <LoadError message={formError} />}
        <AuthSubmit submitting={submitting} label="Send reset link" loadingLabel="Sending…" />
      </form>
      <AuthFooter>
        <AuthLink href="/login" className={standaloneLinkClasses}>
          Back to sign in
        </AuthLink>
      </AuthFooter>
    </AuthCard>
  );
}
