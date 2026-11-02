"use client";

import { useId, type ReactNode } from "react";
import { Check } from "lucide-react";
import { FOCUS_RING } from "@/components/ui/button";
import { FlashMessage, useFlash, type FlashState } from "@/components/ui/flash";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { cn } from "@/components/ui/utils";

export const linkClasses = cn(
  "rounded-tag text-brand underline underline-offset-2 hover:text-brand-hover",
  FOCUS_RING.canvas,
);

export const SECTION_CLASS = "scroll-mt-24 border-t border-line pt-8 first:border-t-0 first:pt-0";

export function SettingsSection({
  title,
  meta,
  id,
  children,
}: {
  title: string;
  meta?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className={SECTION_CLASS}>
      <h2 id={headingId} className="text-h2 text-fg">
        {title}
        {meta && <span className="ml-2 text-caption text-fg-muted tabular-nums">{meta}</span>}
      </h2>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

export function FormSkeleton({ fields }: { fields: number }) {
  return (
    <SkeletonRegion>
      <div className="space-y-4">
        {Array.from({ length: fields }, (_, index) => (
          <div key={index} className="space-y-2">
            <Skeleton className="w-24" />
            <Skeleton className="h-11" />
          </div>
        ))}
        <Skeleton className="h-11 w-28" />
      </div>
    </SkeletonRegion>
  );
}

export function SectionFallback({
  failed,
  what,
  fields,
  onRetry,
}: {
  failed: boolean;
  what: string;
  fields: number;
  onRetry: () => Promise<unknown>;
}) {
  if (failed) return <LoadError message={`Couldn't load ${what}.`} onRetry={onRetry} />;
  return <FormSkeleton fields={fields} />;
}

export function useSavedMessage(): [FlashState, (message: string | null) => void] {
  const [message, flash] = useFlash();
  return [message, (text) => flash(text ?? "")];
}

export function StatusLine({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-ui text-success">
      <Check size={16} aria-hidden="true" className="shrink-0" />
      {children}
    </p>
  );
}

/** Status text on the left, actions on the right (secondary first, primary last). */
export function SaveRow({ saved, children }: { saved: FlashState; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="min-w-0 flex-1">
        <FlashMessage message={saved} />
      </div>
      {children}
    </div>
  );
}
