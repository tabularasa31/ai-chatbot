"use client";

import { useEffect, useState, type ReactNode } from "react";
import { cn } from "./utils";

const REVEAL_DELAY_MS = 200;

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("h-4 rounded-control bg-surface-alt", className)} />;
}

/** Renders nothing for the first 200ms so fast loads never flash placeholders. */
export function SkeletonRegion({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), REVEAL_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div role="status" aria-busy="true" aria-label="Loading">
      {visible && children}
    </div>
  );
}

const NAME_WIDTHS = ["w-2/5", "w-1/2", "w-3/5"];

/** Row-shaped placeholder for the Knowledge lists; a row is as tall as a real one so the list does not jump on load. */
export function RowListSkeleton({ rows, caption = true, className }: { rows: number; caption?: boolean; className?: string }) {
  return (
    <SkeletonRegion>
      <div className={cn("divide-y divide-line", className)}>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className={cn("flex items-center justify-between gap-6", caption ? "py-4" : "h-14")}>
            <div className="min-w-0 flex-1 space-y-1">
              <Skeleton className={cn("h-6", NAME_WIDTHS[index % NAME_WIDTHS.length])} />
              {caption && <Skeleton className="w-24" />}
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <Skeleton className="h-5 w-20" />
              <Skeleton className="h-8 w-8" />
              <Skeleton className="h-8 w-8" />
            </div>
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

/** FAQ-shaped placeholder: question, answer and meta bars over an action row, as tall as a real entry. */
export function FaqListSkeleton({ rows, className }: { rows: number; className?: string }) {
  return (
    <SkeletonRegion>
      <div className={cn("divide-y divide-line", className)}>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="py-4">
            <div className="space-y-1">
              <Skeleton className="h-6 w-3/5" />
              <Skeleton className="h-5 w-4/5" />
              <Skeleton className="h-4 w-24" />
            </div>
            <div className="mt-2 flex h-11 items-center justify-between">
              <Skeleton className="h-8 w-12" />
              <Skeleton className="h-8 w-20" />
            </div>
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}
