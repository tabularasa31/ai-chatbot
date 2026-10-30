import type { ReactNode } from "react";
import Link from "next/link";
import { AuthCard, AuthHeading } from "./AuthCard";
import { fullWidthLinkButtonClasses } from "./link-classes";

type BrokenLinkProps = {
  title: string;
  explanation: string;
  action: { href: string; label: string };
  centered?: boolean;
  children?: ReactNode;
};

export function BrokenLink({ title, explanation, action, centered, children }: BrokenLinkProps) {
  return (
    <AuthCard centered={centered}>
      <AuthHeading moveFocus>{title}</AuthHeading>
      <p className="mb-6 text-body text-fg-muted">{explanation}</p>
      <Link href={action.href} className={fullWidthLinkButtonClasses}>
        {action.label}
      </Link>
      {children}
    </AuthCard>
  );
}
