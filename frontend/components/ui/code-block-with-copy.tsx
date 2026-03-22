"use client";

import { useState } from "react";
import { cn } from "@/components/ui/utils";

type CodeBlockWithCopyProps = {
  code: string;
  copyLabel?: string;
  copiedLabel?: string;
  tone?: "dark" | "light";
  preClassName?: string;
  buttonClassName?: string;
  containerClassName?: string;
};

export function CodeBlockWithCopy({
  code,
  copyLabel = "Copy code",
  copiedLabel = "Copied!",
  tone = "dark",
  preClassName,
  buttonClassName,
  containerClassName,
}: CodeBlockWithCopyProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className={cn("relative", containerClassName)}>
      <button
        type="button"
        onClick={handleCopy}
        aria-label={copied ? copiedLabel : copyLabel}
        title={copied ? copiedLabel : copyLabel}
        className={cn(
          "absolute right-2.5 top-2.5 z-10 inline-flex h-7 w-7 items-center justify-center rounded-md border transition-colors",
          tone === "dark" &&
            "border-slate-700/50 bg-slate-800/80 text-slate-200 hover:bg-slate-700/90",
          tone === "light" &&
            "border-slate-300 bg-white/90 text-slate-500 hover:bg-white hover:text-slate-700",
          buttonClassName,
        )}
      >
        {copied ? (
          <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
            <path
              d="M5 12.5 9.5 17 19 7.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
            <rect
              x="9"
              y="9"
              width="10"
              height="10"
              rx="2"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            />
            <rect
              x="5"
              y="5"
              width="10"
              height="10"
              rx="2"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              opacity="0.75"
            />
          </svg>
        )}
      </button>
      <pre
        className={cn(
          "text-xs p-4 pr-12 rounded-lg overflow-x-auto whitespace-pre",
          tone === "dark" && "bg-slate-900 text-slate-100",
          tone === "light" && "bg-slate-100 text-slate-800",
          preClassName,
        )}
      >
        {code}
      </pre>
    </div>
  );
}
