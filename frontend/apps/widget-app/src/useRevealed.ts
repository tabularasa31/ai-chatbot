import { useEffect, useState } from "react";

// Short waits finish before the visitor can read an indicator, so showing it
// immediately only makes it flicker.
const REVEAL_DELAY_MS = 200;

export function useRevealed(active: boolean): boolean {
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    if (!active) {
      setRevealed(false);
      return;
    }
    const id = setTimeout(() => setRevealed(true), REVEAL_DELAY_MS);
    return () => clearTimeout(id);
  }, [active]);

  return active && revealed;
}
