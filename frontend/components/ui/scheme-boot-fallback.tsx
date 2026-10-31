"use client";

import { useLayoutEffect } from "react";
import { ensureSchemeBooted } from "@/lib/scheme";

// Next 14 answers a page that calls notFound() with an error shell lacking the root layout's <head>, and the client-rendered copy of the head script never executes. Rendered first in <body> so this layout effect runs before any other component reads styles.
export function SchemeBootFallback() {
  useLayoutEffect(ensureSchemeBooted, []);
  return null;
}
