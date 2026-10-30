import { FOCUS_RING } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";

export const CONTAINER = "mx-auto w-full max-w-6xl px-4 sm:px-6";

const TRANSITION = "transition-colors";

export const textLinkClasses = cn(
  "rounded-tag text-brand underline underline-offset-2 hover:text-brand-hover",
  TRANSITION,
  FOCUS_RING.canvas,
);

export const standaloneLinkClasses = cn(textLinkClasses, "inline-flex min-h-11 items-center");

export const navLinkClasses = cn(
  "inline-flex h-11 items-center rounded-control px-3 text-ui font-medium text-fg-muted hover:text-fg",
  TRANSITION,
  FOCUS_RING.canvas,
);
