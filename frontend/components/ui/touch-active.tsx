"use client";

import { useEffect } from "react";

/** iOS Safari applies `:active` only when the document has a touch listener. */
export function TouchActiveEnabler() {
  useEffect(() => {
    const noop = () => {};
    document.addEventListener("touchstart", noop, { passive: true });
    return () => document.removeEventListener("touchstart", noop);
  }, []);
  return null;
}
