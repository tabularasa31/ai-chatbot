import type { ButtonHTMLAttributes, MouseEvent } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "./utils";

const FOCUS_RING_BASE = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2";

export const FOCUS_RING = {
  canvas: `${FOCUS_RING_BASE} focus-visible:ring-offset-canvas`,
  surface: `${FOCUS_RING_BASE} focus-visible:ring-offset-surface`,
  chrome: "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chrome-accent focus-visible:ring-offset-2 focus-visible:ring-offset-chrome",
} as const;

/** Hover, press and focus of a 32px plate inside a 44px chrome control whose `<button>` carries `group`. */
export const CHROME_PLATE =
  "text-chrome-fg-muted transition-[color,background-color,transform] group-hover:bg-chrome-hover group-hover:text-chrome-fg group-active:scale-press-sm group-active:bg-chrome-press group-active:text-chrome-fg group-disabled:bg-transparent group-disabled:text-chrome-line-strong group-disabled:group-active:scale-100 group-focus-visible:ring-2 group-focus-visible:ring-chrome-accent group-focus-visible:ring-offset-2 group-focus-visible:ring-offset-chrome";

const VARIANTS = {
  primary:
    "bg-brand text-brand-on hover:bg-brand-hover active:bg-brand-active disabled:bg-surface-alt disabled:text-fg-faint",
  secondary:
    "border border-line-button bg-surface text-fg hover:bg-surface-alt active:border-line-strong active:bg-line disabled:border-transparent disabled:bg-surface-alt disabled:text-fg-faint",
  destructive:
    "bg-danger text-danger-on hover:bg-danger-hover active:bg-danger-active disabled:bg-surface-alt disabled:text-fg-faint",
  ghost: "text-fg-muted hover:bg-surface-alt active:bg-line active:text-fg disabled:bg-transparent disabled:text-line-strong",
} as const;

const PRESS =
  "active:scale-press disabled:active:scale-100 aria-disabled:active:scale-100 aria-busy:active:scale-100 aria-busy:pointer-events-none";

const SIZES = {
  default: "gap-2 px-4",
  icon: "w-11",
} as const;

type ButtonStyle = {
  variant: keyof typeof VARIANTS;
  size?: keyof typeof SIZES;
  /** Surface the button sits on, for the focus ring offset. */
  offset?: keyof typeof FOCUS_RING;
};

/** Class string for elements that must look like a button but are not one, such as links. */
export function buttonClasses({ variant, size = "default", offset = "canvas" }: ButtonStyle): string {
  return cn(
    "inline-flex h-11 shrink-0 items-center justify-center rounded-control text-ui font-medium touch-manipulation transition-[color,background-color,border-color,transform] disabled:cursor-not-allowed",
    FOCUS_RING[offset],
    VARIANTS[variant],
    PRESS,
    SIZES[size],
  );
}

/** `busy` overlays a spinner on the hidden content, so the width never changes; clicks are ignored and focus is kept. */
export function Button({
  variant,
  size,
  offset,
  className,
  type = "button",
  busy = false,
  onClick,
  children,
  ...props
}: ButtonStyle & { busy?: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  function handleClick(event: MouseEvent<HTMLButtonElement>) {
    if (!busy) onClick?.(event);
  }

  return (
    <button
      type={type}
      className={cn(buttonClasses({ variant, size, offset }), busy && "relative", className)}
      aria-busy={busy || undefined}
      onClick={handleClick}
      {...props}
    >
      {busy && <Loader2 size={16} aria-hidden="true" className="absolute inset-0 m-auto animate-spin" />}
      <span className={cn("inline-flex items-center gap-2", busy && "opacity-0")}>{children}</span>
    </button>
  );
}
