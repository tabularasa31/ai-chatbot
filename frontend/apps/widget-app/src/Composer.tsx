import { ArrowUp, Loader2 } from "lucide-react";
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useState, type RefObject } from "react";
import type { ComposerCounter } from "./composerLimit";
import { t } from "./strings";
import { cn } from "./utils";

const MAX_ROWS = 5;
const MAX_PANEL_FRACTION = 0.4;
const COARSE_POINTER = "(pointer: coarse)";
const COUNTER_ID = "composer-counter";

type ComposerProps = {
  value: string;
  onValueChange: (value: string) => void;
  onSend: () => void;
  locale: string | null;
  limit: number;
  counter: ComposerCounter;
  canSubmit: boolean;
  loading: boolean;
  textareaRef: RefObject<HTMLTextAreaElement>;
  panelRef: RefObject<HTMLElement>;
};

function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => window.matchMedia(COARSE_POINTER).matches);
  useEffect(() => {
    const query = window.matchMedia(COARSE_POINTER);
    const update = () => setCoarse(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return coarse;
}

function fitToContent(field: HTMLTextAreaElement, panel: HTMLElement | null): boolean {
  const style = getComputedStyle(field);
  const line = parseFloat(style.lineHeight);
  const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
  const oneRow = line + padding + border;
  const panelCap = panel ? panel.clientHeight * MAX_PANEL_FRACTION : Infinity;
  const maxHeight = Math.max(oneRow, Math.min(line * MAX_ROWS + padding + border, panelCap));

  field.style.height = "auto";
  const wanted = field.scrollHeight + border;
  field.style.height = `${Math.min(wanted, maxHeight)}px`;
  field.style.overflowY = wanted > maxHeight ? "auto" : "hidden";
  return wanted > oneRow + 1;
}

export function Composer({
  value,
  onValueChange,
  onSend,
  locale,
  limit,
  counter,
  canSubmit,
  loading,
  textareaRef,
  panelRef,
}: ComposerProps) {
  const coarse = useCoarsePointer();
  const [multiline, setMultiline] = useState(false);
  const over = counter.state === "over";

  useLayoutEffect(() => {
    const field = textareaRef.current;
    if (!field) return;
    const refit = () => setMultiline(fitToContent(field, panelRef.current));
    refit();
    window.addEventListener("resize", refit);
    return () => window.removeEventListener("resize", refit);
  }, [value, textareaRef, panelRef]);

  function handleKeyDown(event: JSX.TargetedKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.isComposing) return;
    const modified = event.ctrlKey || event.metaKey;
    const sends = modified || (!coarse && !event.shiftKey);
    if (!sends) return;
    event.preventDefault();
    onSend();
  }

  return (
    <>
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => onValueChange(e.currentTarget.value)}
          onKeyDown={handleKeyDown}
          dir="auto"
          aria-label={t(locale, "composer_label")}
          aria-invalid={over || undefined}
          aria-describedby={counter.state === "hidden" ? undefined : COUNTER_ID}
          placeholder={t(locale, "composer_placeholder")}
          enterkeyhint={coarse ? "enter" : "send"}
          autoComplete="off"
          autoCapitalize="sentences"
          className={cn(
            "composer-input min-h-10 min-w-0 flex-1 resize-none overscroll-contain border border-line bg-surface-alt px-4 text-fg placeholder:text-fg-faint dark:bg-canvas",
            multiline ? "rounded-bubble" : "rounded-full",
          )}
        />
        <button
          type="button"
          onClick={onSend}
          disabled={!loading && !canSubmit}
          aria-disabled={loading || undefined}
          aria-busy={loading}
          aria-label={t(locale, "send_message")}
          className="accent-fill accent-press hit-44-send relative mb-0.5 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-fg-faint"
        >
          {loading ? (
            <Loader2 size={16} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
          ) : (
            <ArrowUp size={16} aria-hidden="true" />
          )}
        </button>
      </div>
      {counter.state === "left" ? (
        <p id={COUNTER_ID} aria-hidden="true" className="mt-1 text-caption tabular-nums text-fg-muted">
          {t(locale, "composer_chars_left", { n: String(counter.left) })}
        </p>
      ) : null}
      {counter.state === "over" ? (
        <p id={COUNTER_ID} role="status" className="mt-1 text-caption tabular-nums text-danger">
          {t(locale, "composer_too_long", { n: String(counter.over), max: String(limit) })}
        </p>
      ) : null}
    </>
  );
}
