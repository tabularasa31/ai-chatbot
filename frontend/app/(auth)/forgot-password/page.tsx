"use client";

import { useState } from "react";
import { flushSync } from "react-dom";
import { api } from "@/lib/api";
import { AuthCard, AuthFooter, AuthHeading, AuthSubmit, StandaloneLink } from "@/components/auth/AuthCard";
import { AuthField, EMAIL_INPUT_PROPS } from "@/components/auth/AuthField";
import { applyFieldErrors, emailError, focusField, formErrorMessage, type FieldErrors } from "@/components/auth/auth-rules";
import { Button } from "@/components/ui/button";
import { LoadError } from "@/components/ui/load-error";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<FieldErrors<"email">>({ email: null });
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);
    const next = { email: emailError(email) };
    applyFieldErrors(setErrors, next);
    if (next.email) return;

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

  function editEmail() {
    flushSync(() => setSubmitted(false));
    focusField("email");
  }

  if (submitted) {
    return (
      <AuthCard centered>
        <AuthHeading moveFocus>Check your email</AuthHeading>
        <p className="mb-6 text-body text-fg-muted">
          If an account exists for <span className="break-all font-medium text-fg">{email}</span>, we sent a link to
          reset the password.
        </p>
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
            setErrors({ email: null });
          }}
          error={errors.email}
        />
        {formError && <LoadError message={formError} />}
        <AuthSubmit submitting={submitting} label="Send reset link" loadingLabel="Sending…" />
      </form>
      <AuthFooter>
        <StandaloneLink href="/login">Back to sign in</StandaloneLink>
      </AuthFooter>
    </AuthCard>
  );
}
