"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";

const FLASH_DURATION_MS = 4000;

export function useFlash(): [string, (message: string) => void] {
  const [message, setMessage] = useState("");
  const timer = useRef<number>();

  const flash = useCallback((next: string) => {
    window.clearTimeout(timer.current);
    setMessage(next);
    timer.current = window.setTimeout(() => setMessage(""), FLASH_DURATION_MS);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return [message, flash];
}

/** The live region stays mounted so screen readers announce the text when it appears. */
export function FlashMessage({ message }: { message: string }) {
  return (
    <p aria-live="polite" className="inline-flex items-center gap-1 text-ui text-success">
      {message && (
        <>
          <Check size={16} aria-hidden="true" />
          {message}
        </>
      )}
    </p>
  );
}
