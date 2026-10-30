"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { buttonClasses } from "@/components/ui/button";

type NotFoundContentProps = {
  action: { href: string; label: string };
  moveFocus?: boolean;
};

export function NotFoundContent({ action, moveFocus }: NotFoundContentProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (moveFocus) heading.current?.focus();
  }, [moveFocus]);

  return (
    <div className="max-w-measure">
      <h1 ref={heading} tabIndex={-1} className="text-h1 text-fg focus:outline-none">
        Page not found
      </h1>
      <p className="mt-2 text-body text-fg-muted">This page doesn&apos;t exist or has moved.</p>
      <Link href={action.href} className={`${buttonClasses({ variant: "primary" })} mt-6 w-full sm:w-auto`}>
        {action.label}
      </Link>
    </div>
  );
}
