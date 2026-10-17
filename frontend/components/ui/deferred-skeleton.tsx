"use client";

import { useEffect, useState, type ReactNode } from "react";

const SKELETON_DELAY_MS = 200;

export function DeferredSkeleton({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setVisible(true), SKELETON_DELAY_MS);
    return () => clearTimeout(id);
  }, []);

  return <div aria-busy="true">{visible ? children : null}</div>;
}
