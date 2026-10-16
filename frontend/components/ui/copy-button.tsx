"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "./button";

const COPIED_RESET_MS = 4000;

type CopyButtonProps = {
  text: string;
  label: string;
  variant: "primary" | "secondary";
};

export function CopyButton({ text, label, variant }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setFailed(false);
      setCopied(true);
    } catch {
      setCopied(false);
      setFailed(true);
    }
  }

  const Icon = copied ? Check : Copy;

  return (
    <>
      <Button variant={variant} onClick={handleCopy}>
        <Icon size={16} aria-hidden />
        <span aria-live="polite">{copied ? "Copied" : label}</span>
      </Button>
      {failed && (
        <span role="alert" className="text-ui text-danger">
          Copy failed. Select the text and copy it manually.
        </span>
      )}
    </>
  );
}
