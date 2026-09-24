// The API stamps naive UTC datetimes without a zone designator; read those as
// UTC rather than as the browser's local time. Strings that already carry a
// Z/offset, or that aren't datetimes (no "T"), are left as is.
export function parseApiDate(iso: string): Date {
  return new Date(/T/.test(iso) && !/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? `${iso}Z` : iso);
}

function format(
  iso: string | null | undefined,
  fallback: string,
  toString: (date: Date) => string,
): string {
  if (!iso) return fallback;
  return toString(parseApiDate(iso));
}

export function formatDateTime(iso: string | null | undefined, fallback = "—"): string {
  return format(iso, fallback, (d) => d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }));
}

// Bare toLocaleString() with no options — for call sites that predate this helper.
export function formatDateTimeLocale(iso: string | null | undefined, fallback = "—"): string {
  return format(iso, fallback, (d) => d.toLocaleString());
}

export function formatDate(iso: string | null | undefined, fallback = "—"): string {
  return format(iso, fallback, (d) =>
    d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }),
  );
}

export function formatTime(iso: string | null | undefined, fallback = "—"): string {
  return format(iso, fallback, (d) => d.toLocaleTimeString(undefined, { timeStyle: "short" }));
}
