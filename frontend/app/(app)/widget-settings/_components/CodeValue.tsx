import type { ReactNode } from "react";

export function CodeValue({ children }: { children: ReactNode }) {
  return (
    <code className="flex min-h-11 min-w-0 flex-1 items-center break-anywhere rounded-control border border-line bg-surface-alt px-3 font-mono text-caption text-fg">
      {children}
    </code>
  );
}
