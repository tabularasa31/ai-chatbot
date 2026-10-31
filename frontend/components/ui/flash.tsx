"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";

const FLASH_DURATION_MS = 4000;
const FLASH_FADE_MS = 120;

export type FlashState = { text: string; id: number };

export function useFlash(): [FlashState, (message: string) => void] {
  const [state, setState] = useState<FlashState>({ text: "", id: 0 });
  const timer = useRef<number>();

  const flash = useCallback((text: string) => {
    window.clearTimeout(timer.current);
    setState((previous) => ({ text, id: previous.id + 1 }));
    timer.current = window.setTimeout(() => setState((previous) => ({ ...previous, text: "" })), FLASH_DURATION_MS);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return [state, flash];
}

/**
 * The live region stays mounted so screen readers announce the text when it appears.
 * The outer span fades in once; the inner one is keyed so a new flash restarts the fade-out at full opacity.
 */
export function FlashMessage({ message }: { message: FlashState }) {
  return (
    <p aria-live="polite" className="inline-flex min-h-5 items-center text-ui text-success">
      {message.text && (
        <span className="inline-flex animate-fade-in items-center">
          <span
            key={message.id}
            className="inline-flex animate-fade-out items-center gap-1"
            style={{ animationDelay: `${FLASH_DURATION_MS - FLASH_FADE_MS}ms` }}
          >
            <Check size={16} aria-hidden="true" />
            {message.text}
          </span>
        </span>
      )}
    </p>
  );
}
