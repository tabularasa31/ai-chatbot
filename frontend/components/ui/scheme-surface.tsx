"use client";

import { useLayoutEffect, type ReactNode } from "react";
import { resolveScheme } from "@/lib/scheme";

/** Re-applies the stored scheme on mount: client navigation from the still-light docs left `<html>` without `dark`. */
export function SchemeSurface({ children }: { children: ReactNode }) {
  useLayoutEffect(resolveScheme, []);
  return (
    <div data-scheme-surface className="min-h-screen bg-canvas">
      {children}
    </div>
  );
}
