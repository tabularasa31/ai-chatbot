"use client";

import { useEffect, useRef, type HTMLAttributes, type ReactNode } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { Logo } from "@/components/Logo";
import { Button, FOCUS_RING, buttonClasses } from "@/components/ui/button";
import { ThemeSwitch } from "@/components/ui/theme-switch";
import { cn } from "@/components/ui/utils";
import { resolveScheme } from "@/lib/scheme";

type AuthCardProps = { centered?: boolean } & HTMLAttributes<HTMLDivElement>;

export function AuthCard({ centered, className, children, ...card }: AuthCardProps) {
  useEffect(() => {
    resolveScheme();
  }, []);

  return (
    <div
      data-scheme-surface
      className="flex min-h-dvh justify-center bg-canvas px-4 py-8 text-fg sm:items-center sm:p-4"
    >
      <div className="w-full max-w-md">
        <div className="mb-6">
          <Link
            href="/"
            aria-label="Chat9 home"
            className={cn("inline-flex min-h-11 items-center rounded-tag", FOCUS_RING.canvas)}
          >
            <Logo className="[&>span]:text-fg" />
          </Link>
        </div>
        <div
          className={cn(
            "rounded-surface border border-line bg-surface p-6 sm:p-8",
            centered && "text-center",
            className,
          )}
          {...card}
        >
          {children}
        </div>
        <ThemeSwitch className="mt-6" />
      </div>
    </div>
  );
}

export function AuthSkeleton() {
  return (
    <AuthCard aria-busy="true">
      <div className="h-6 rounded-tag bg-surface-alt" />
    </AuthCard>
  );
}

export function AuthHeading({ children, moveFocus }: { children: string; moveFocus?: boolean }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (moveFocus) heading.current?.focus();
  }, [moveFocus, children]);

  return (
    <h1 ref={heading} tabIndex={-1} className="mb-6 text-h1 text-fg focus:outline-none">
      {children}
    </h1>
  );
}

const authLinkClasses = cn(
  "rounded-tag text-brand underline underline-offset-2 transition-colors hover:text-brand-hover",
  FOCUS_RING.surface,
);

export const standaloneLinkClasses = cn(authLinkClasses, "inline-flex min-h-11 items-center");

export const fullWidthLinkButtonClasses = cn(buttonClasses({ variant: "primary", offset: "surface" }), "w-full");

export function AuthLink({ href, className, children }: { href: string; className?: string; children: ReactNode }) {
  return (
    <Link href={href} className={cn(authLinkClasses, className)}>
      {children}
    </Link>
  );
}

export function AuthFooter({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <p className={cn("mt-4 flex flex-wrap items-center gap-x-1 text-body text-fg-muted", className)}>{children}</p>
  );
}

export function AuthSubmit({
  submitting,
  label,
  loadingLabel,
}: {
  submitting: boolean;
  label: string;
  loadingLabel: string;
}) {
  return (
    <Button type="submit" variant="primary" offset="surface" aria-busy={submitting} className="mt-2 w-full">
      {submitting && <Loader2 size={16} aria-hidden="true" className="animate-spin" />}
      {submitting ? loadingLabel : label}
    </Button>
  );
}
