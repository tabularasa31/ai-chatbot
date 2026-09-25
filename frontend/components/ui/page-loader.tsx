import { cn } from "./utils";

export function PageLoader({
  textClassName = "text-slate-500 text-sm",
}: {
  textClassName?: string;
}) {
  return (
    <div className="flex items-center justify-center py-16">
      <div className={cn("animate-pulse", textClassName)}>Loading…</div>
    </div>
  );
}
