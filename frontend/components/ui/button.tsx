import type { ButtonHTMLAttributes } from "react";
import { cn } from "./utils";

const FOCUS_RING_BASE = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2";

export const FOCUS_RING = {
  canvas: `${FOCUS_RING_BASE} focus-visible:ring-offset-canvas`,
  surface: `${FOCUS_RING_BASE} focus-visible:ring-offset-surface`,
  chrome: "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chrome-accent focus-visible:ring-offset-2 focus-visible:ring-offset-chrome",
} as const;

const VARIANTS = {
  primary: "bg-brand text-brand-on hover:bg-brand-hover active:bg-brand-active",
  secondary: "border border-line-strong bg-surface text-fg hover:bg-surface-alt active:bg-surface-alt",
  ghost: "text-fg-muted hover:bg-surface-alt active:bg-surface-alt",
} as const;

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
    "inline-flex h-11 shrink-0 items-center justify-center rounded-control text-ui font-medium transition-colors",
    FOCUS_RING[offset],
    VARIANTS[variant],
    SIZES[size],
  );
}

export function Button({
  variant,
  size,
  offset,
  className,
  type = "button",
  ...props
}: ButtonStyle & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={cn(buttonClasses({ variant, size, offset }), className)} {...props} />;
}
