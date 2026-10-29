"use client";

import { useEffect, useRef, useState } from "react";
import type { UnansweredDismissReason } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { RadioGroup, type RadioOption } from "@/components/ui/choice";

type ReasonChoice = UnansweredDismissReason | "";

export const DISMISS_REASON_LABELS: Record<UnansweredDismissReason, string> = {
  not_our_product: "Not about our product",
  should_not_answer: "Bot should not answer",
  other: "Other",
};

const REASON_OPTIONS: readonly RadioOption<ReasonChoice>[] = [
  { value: "not_our_product", label: DISMISS_REASON_LABELS.not_our_product, description: "" },
  { value: "should_not_answer", label: DISMISS_REASON_LABELS.should_not_answer, description: "" },
  { value: "other", label: DISMISS_REASON_LABELS.other, description: "" },
];

export function NotNeededForm({
  name,
  busy,
  onSubmit,
  onCancel,
}: {
  name: string;
  busy: boolean;
  onSubmit: (reason: UnansweredDismissReason) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState<ReasonChoice>("");
  const form = useRef<HTMLDivElement>(null);

  useEffect(() => {
    form.current?.querySelector("input")?.focus();
  }, []);

  useEffect(() => {
    const element = form.current;
    if (!element) return;
    const cancelOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    element.addEventListener("keydown", cancelOnEscape);
    return () => element.removeEventListener("keydown", cancelOnEscape);
  }, [onCancel]);

  function submit() {
    if (reason !== "") onSubmit(reason);
  }

  return (
    <div ref={form} className="-mt-2.5 w-fit shrink-0 space-y-3">
      <RadioGroup label="Reason" name={name} options={REASON_OPTIONS} value={reason} onChange={setReason} />
      <div className="flex justify-end gap-3">
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" busy={busy} disabled={reason === ""} onClick={submit}>
          Mark as not needed
        </Button>
      </div>
    </div>
  );
}
