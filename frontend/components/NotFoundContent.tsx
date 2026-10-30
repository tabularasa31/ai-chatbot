"use client";

import Link from "next/link";
import { buttonClasses } from "@/components/ui/button";

type NotFoundContentProps = {
  action: { href: string; label: string };
};

export function NotFoundContent({ action }: NotFoundContentProps) {
  return (
    <div className="max-w-measure">
      <h1 className="text-h1 text-fg">Page not found</h1>
      <p className="mt-2 text-body text-fg-muted">This page doesn&apos;t exist or has moved.</p>
      <Link href={action.href} className={`${buttonClasses({ variant: "primary" })} mt-6 w-full sm:w-auto`}>
        {action.label}
      </Link>
    </div>
  );
}
