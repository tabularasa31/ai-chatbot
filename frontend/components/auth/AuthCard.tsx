"use client";

import { ReactNode } from "react";
import { strings } from "@/lib/strings";

const cardShell =
  "bg-nd-surface border border-nd-border rounded-lg shadow-md p-8";

function AuthShell({
  children,
  centered,
}: {
  children: ReactNode;
  centered?: boolean;
}) {
  return (
    <div className="min-h-screen bg-nd-base flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className={centered ? `${cardShell} text-center` : cardShell}>{children}</div>
      </div>
    </div>
  );
}

/** Neon Dusk dark theme — shared layout and form styles for auth pages */
export function AuthCard({ children }: { children: ReactNode }) {
  return <AuthShell>{children}</AuthShell>;
}

/** Centered card for success/error states (e.g. forgot-password success, verify) */
export function AuthCardCentered({ children }: { children: ReactNode }) {
  return <AuthShell centered>{children}</AuthShell>;
}

/** Validation messages use service UI language (see lib/strings.ts) */
export const validationHandlers = {
  email: {
    onInvalid: (e: React.FormEvent<HTMLInputElement>) => {
      const el = e.currentTarget;
      if (el.validity.typeMismatch) el.setCustomValidity(strings.validation.emailInvalid);
      else if (el.validity.valueMissing) el.setCustomValidity(strings.validation.emailRequired);
      else el.setCustomValidity("");
    },
    onInput: (e: React.FormEvent<HTMLInputElement>) => e.currentTarget.setCustomValidity(""),
  },
  required: {
    onInvalid: (e: React.FormEvent<HTMLInputElement>) => {
      const el = e.currentTarget;
      if (el.validity.valueMissing) el.setCustomValidity(strings.validation.fieldRequired);
      else el.setCustomValidity("");
    },
    onInput: (e: React.FormEvent<HTMLInputElement>) => e.currentTarget.setCustomValidity(""),
  },
} as const;

export const authStyles = {
  heading: "text-2xl font-semibold text-nd-text mb-6",
  headingSm: "text-2xl font-semibold text-nd-text mb-2",
  subtext: "text-nd-text/80 text-sm mb-6",
  label: "block text-sm font-medium text-nd-text/80 mb-1",
  input:
    "w-full px-3 py-2 bg-nd-base border border-nd-border rounded-md text-nd-text placeholder-nd-text/40 focus:outline-none focus:ring-2 focus:ring-nd-accent focus:ring-offset-2 focus:ring-offset-nd-base focus:border-transparent",
  button:
    "w-full py-2 px-4 bg-nd-accent text-nd-base font-medium rounded-md hover:bg-nd-accent-hover hover:scale-105 transition-all focus:outline-none focus:ring-2 focus:ring-nd-accent focus:ring-offset-2 focus:ring-offset-nd-base disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100",
  /** Same visual as `button`, for `<Link>` CTAs (verify success/error, etc.) */
  ctaLink:
    "inline-block w-full py-2 px-4 bg-nd-accent text-nd-base font-medium rounded-md hover:bg-nd-accent-hover hover:scale-105 transition-all text-center no-underline focus:outline-none focus:ring-2 focus:ring-nd-accent focus:ring-offset-2 focus:ring-offset-nd-base",
  error: "text-nd-danger text-sm bg-nd-danger/10 border border-nd-danger/30 px-3 py-2 rounded-md",
  success: "text-nd-success text-sm bg-nd-success/10 border border-nd-success/30 px-3 py-2 rounded-md",
  link: "text-nd-accent hover:underline",
  footer: "mt-4 text-center text-nd-text/60 text-sm",
} as const;
