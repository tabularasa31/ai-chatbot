import { cn } from "./utils";

const MAX_DISPLAYED = 99;

const TONES = {
  brand: "bg-chrome-accent text-chrome-accent-fg",
  quiet: "bg-chrome-line text-chrome-fg",
} as const;

export function CountBadge({
  count,
  tone,
  meaning,
}: {
  count: number;
  tone: keyof typeof TONES;
  /** Spoken after the number, e.g. "need attention". */
  meaning: string;
}) {
  if (count <= 0) return null;
  return (
    <>
      <span
        aria-hidden="true"
        className={cn(
          "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-caption font-medium tabular-nums",
          TONES[tone],
        )}
      >
        {count > MAX_DISPLAYED ? `${MAX_DISPLAYED}+` : count}
      </span>
      <span className="sr-only">
        , {count} {meaning}
      </span>
    </>
  );
}
