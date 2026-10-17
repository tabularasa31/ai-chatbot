import { Loader2 } from "lucide-react";
import type { ReactNode, RefObject } from "react";

const VARIANT_CLASSES = {
  primary: "bg-brand text-brand-on hover:bg-brand-hover active:bg-brand-active",
  secondary: "border border-line-strong bg-surface text-fg hover:bg-surface-alt",
} as const;

type ActionButtonProps = {
  variant: keyof typeof VARIANT_CLASSES;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
  buttonRef?: RefObject<HTMLButtonElement>;
  children: ReactNode;
};

export function ActionButton({ variant, onClick, busy = false, disabled = false, buttonRef, children }: ActionButtonProps) {
  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={onClick}
      disabled={busy || disabled}
      aria-busy={busy}
      className={`inline-flex h-11 items-center justify-center gap-2 rounded-control px-4 text-ui font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANT_CLASSES[variant]}`}
    >
      {busy ? <Loader2 size={16} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : null}
      {children}
    </button>
  );
}
