"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { api, markSession, takeSignOutReason } from "@/lib/api";
import { AuthCard, AuthFooter, AuthHeading, AuthLink, AuthSkeleton, AuthSubmit, standaloneLinkClasses } from "@/components/auth/AuthCard";
import { AuthField, EMAIL_INPUT_PROPS } from "@/components/auth/AuthField";
import {
  emailError,
  focusFirstError,
  formErrorMessage,
  hasErrors,
  isApiStatus,
  type FieldErrors,
} from "@/components/auth/auth-rules";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";

const NO_ERRORS: FieldErrors<"email" | "password"> = { email: null, password: null };

function LoginForm() {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState(NO_ERRORS);
  const [formError, setFormError] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [workspaceDeleted, setWorkspaceDeleted] = useState(false);

  const sessionExpired = searchParams.get("error") === "session_expired";
  const notVerified = unverified || searchParams.get("error") === "email_not_verified";

  // Reading the reason consumes it, so a reload does not repeat the message.
  useEffect(() => {
    if (takeSignOutReason() === "workspace_deleted") setWorkspaceDeleted(true);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setFormError(null);
    setUnverified(false);
    const next = { email: emailError(email), password: password ? null : "Enter your password." };
    setErrors(next);
    if (hasErrors(next)) {
      focusFirstError(next);
      return;
    }

    setSubmitting(true);
    try {
      await api.auth.login(email, password);
      markSession();
      // A full navigation: the app router may replay a cached /dashboard redirect back to /login.
      window.location.replace("/dashboard");
    } catch (err) {
      setSubmitting(false);
      if (isApiStatus(err, 403)) setUnverified(true);
      else if (isApiStatus(err, 401)) setFormError("Email or password is incorrect.");
      else setFormError(formErrorMessage(err));
    }
  }

  return (
    <AuthCard>
      <AuthHeading>Sign in</AuthHeading>
      {workspaceDeleted ? (
        <div className="mb-4">
          <InlineNotice>
            Your workspace and account were deleted. To start over, <AuthLink href="/signup">create an account</AuthLink>.
          </InlineNotice>
        </div>
      ) : sessionExpired ? (
        <div className="mb-4">
          <InlineNotice>Your session expired. Sign in again.</InlineNotice>
        </div>
      ) : (
        notVerified && (
          <div className="mb-4">
            <InlineNotice>
              Verify your email to sign in. Use the link we sent when you created your account.
            </InlineNotice>
          </div>
        )
      )}
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <AuthField
          id="email"
          label="Email"
          {...EMAIL_INPUT_PROPS}
          autoComplete="username"
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
          autoComplete="current-password"
          required
          readOnly={submitting}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setErrors((prev) => ({ ...prev, password: null }));
          }}
          error={errors.password}
        />
        <AuthLink href="/forgot-password" className={`${standaloneLinkClasses} self-start`}>
          Forgot password?
        </AuthLink>
        {formError && <LoadError message={formError} />}
        <AuthSubmit submitting={submitting} label="Sign in" loadingLabel="Signing in…" />
      </form>
      <AuthFooter>
        No account yet?
        <AuthLink href="/signup" className="inline-flex min-h-11 items-center">
          Create account
        </AuthLink>
      </AuthFooter>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<AuthSkeleton />}>
      <LoginForm />
    </Suspense>
  );
}
