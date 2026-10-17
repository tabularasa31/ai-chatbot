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
