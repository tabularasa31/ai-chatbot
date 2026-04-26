"use client";

import { useEffect, useState } from "react";

interface AuthTransitionProps {
  onComplete: () => void;
}

export function AuthTransition({ onComplete }: AuthTransitionProps) {
  const [opacity, setOpacity] = useState(1);

  useEffect(() => {
    // Start fade-out immediately
    const fadeTimer = setTimeout(() => {
      setOpacity(0);
    }, 50); // tiny delay to ensure initial render at opacity 1

    // Call onComplete after animation finishes (400ms)
    const completeTimer = setTimeout(() => {
      onComplete();
    }, 450);

    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(completeTimer);
    };
  }, [onComplete]);

  return (
    <div
      className="fixed inset-0 bg-nd-base pointer-events-none"
      style={{ opacity, transition: "opacity 400ms ease-out", zIndex: 9999 }}
    />
  );
}
