import type { ReactNode } from "react";
import { cn } from "@/components/ui/utils";

type RadioOptionProps = {
  name: string;
  checked: boolean;
  onSelect: () => void;
  className?: string;
  children: ReactNode;
};

export function RadioOption({ name, checked, onSelect, className, children }: RadioOptionProps) {
  return (
    <label className="block cursor-pointer">
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onSelect}
        className="peer sr-only"
      />
      <div
        className={cn(
          "flex min-h-11 flex-col justify-center rounded-control border px-4 py-2 text-ui text-fg transition-colors",
          "peer-focus-visible:ring-2 peer-focus-visible:ring-focus peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-canvas",
          checked ? "border-brand bg-brand-subtle" : "border-line-strong hover:bg-surface-alt active:bg-line",
          className,
        )}
      >
        {children}
      </div>
    </label>
  );
}
