"use client";

import { useEffect } from "react";
import { resolveScheme } from "@/lib/scheme";

export function SchemeSurface() {
  useEffect(() => {
    resolveScheme();
  }, []);
  return <span hidden data-scheme-surface />;
}
