"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "./button";

const COPIED_RESET_MS = 4000;
const COPIED_FADE_MS = 120;
const COPIED_LABEL = "Copied";

type CopyButtonProps = {
  text: string;
  label: string;
  variant: "primary" | "secondary";
};

export function CopyButton({ text, label, variant }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const [clicks, setClicks] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => clearTimeout(timer);
  }, [copied, clicks]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setFailed(false);
      setCopied(true);
      setClicks((previous) => previous + 1);
    } catch {
      setCopied(false);
      setFailed(true);
    }
  }

  const Icon = copied ? Check : Copy;

  return (
    <>
      <Button variant={variant} onClick={handleCopy}>
        <span aria-live="polite" className="sr-only">{copied ? COPIED_LABEL : label}</span>
        <span
          aria-hidden="true"
          key={copied ? clicks : "idle"}
          className={`inline-flex items-center gap-2 ${copied ? "animate-fade-out" : ""}`}
          style={copied ? { animationDelay: `${COPIED_RESET_MS - COPIED_FADE_MS}ms` } : undefined}
        >
          <Icon size={16} aria-hidden />
          <span className="grid">
            <span className="col-start-1 row-start-1">{copied ? COPIED_LABEL : label}</span>
            <span className="invisible col-start-1 row-start-1">{copied ? label : COPIED_LABEL}</span>
          </span>
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
