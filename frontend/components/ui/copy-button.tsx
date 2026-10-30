"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "./button";

const COPIED_RESET_MS = 4000;
const COPIED_LABEL = "Copied";

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
        <span className="grid">
          <span aria-live="polite" className="col-start-1 row-start-1">{copied ? COPIED_LABEL : label}</span>
          <span aria-hidden="true" className="invisible col-start-1 row-start-1">{copied ? label : COPIED_LABEL}</span>
        </span>
      </Button>
      {failed && (
        <span role="alert" className="text-ui text-danger">
          Copy failed. Select the text and copy it manually.
        </span>
      )}
    </>
  );
}
