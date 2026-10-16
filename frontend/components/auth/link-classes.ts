import { FOCUS_RING, buttonClasses } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";

export const authLinkClasses = cn(
  "rounded-tag text-brand underline underline-offset-2 transition-colors duration-[120ms] hover:text-brand-hover motion-reduce:transition-none",
  FOCUS_RING.surface,
);

export const standaloneLinkClasses = cn(authLinkClasses, "inline-flex min-h-11 items-center");

export const fullWidthLinkButtonClasses = cn(buttonClasses({ variant: "primary", offset: "surface" }), "w-full");
