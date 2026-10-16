"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "./utils";

const COPIED_RESET_MS = 4000;

const VARIANT_CLASSES = {
  primary: "bg-brand text-brand-on hover:bg-brand-hover active:bg-brand-active",
  secondary: "border border-line-strong bg-surface text-fg hover:bg-surface-alt active:bg-surface-alt",
} as const;

type CopyButtonProps = {
  text: string;
  label: string;
  variant: keyof typeof VARIANT_CLASSES;
};

export function CopyButton({ text, label, variant }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  async function handleCopy() {
    await navigator.clipboard.writeText(text);
    setCopied(true);
  }

  const Icon = copied ? Check : Copy;

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={cn(
        "inline-flex h-11 items-center justify-center gap-2 rounded-control px-4 text-ui font-medium",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-canvas",
        VARIANT_CLASSES[variant],
      )}
    >
      <Icon size={16} aria-hidden />
      <span aria-live="polite">{copied ? "Copied" : label}</span>
    </button>
  );
}
