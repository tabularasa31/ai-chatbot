// The API stamps naive UTC without a zone designator; read it as UTC rather
// than as the browser's local time. Strings that already carry Z/offset are
// left as is.
export function parseApiDate(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function formatDateTime(iso: string): string {
  return parseApiDate(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

export function formatDate(iso: string): string {
  return parseApiDate(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatTime(iso: string): string {
  return parseApiDate(iso).toLocaleTimeString(undefined, { timeStyle: "short" });
}
