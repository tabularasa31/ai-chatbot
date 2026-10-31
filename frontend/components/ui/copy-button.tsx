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

type CopyState = { status: "idle" } | { status: "copied"; id: number } | { status: "failed" };

export function CopyButton({ text, label, variant }: CopyButtonProps) {
  const [state, setState] = useState<CopyState>({ status: "idle" });
  const copied = state.status === "copied";
  const copiedId = state.status === "copied" ? state.id : null;

  useEffect(() => {
    if (copiedId === null) return;
    const timer = setTimeout(() => setState({ status: "idle" }), COPIED_RESET_MS);
    return () => clearTimeout(timer);
  }, [copiedId]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setState((previous) => ({ status: "copied", id: previous.status === "copied" ? previous.id + 1 : 0 }));
    } catch {
      setState({ status: "failed" });
    }
  }

  const Icon = copied ? Check : Copy;

  return (
    <>
      <Button variant={variant} onClick={handleCopy}>
        <span aria-live="polite" className="sr-only">{copied ? COPIED_LABEL : label}</span>
        <span
          aria-hidden="true"
          key={copiedId ?? "idle"}
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
      {state.status === "failed" && (
        <span role="alert" className="text-ui text-danger">
          Copy failed. Select the text and copy it manually.
        </span>
      )}
    </>
  );
}
