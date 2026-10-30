export const DEFAULT_MAX_CHARS = 1000;
export const COUNTER_FROM = 900;

export type ComposerCounter =
  | { state: "hidden" }
  | { state: "left"; left: number }
  | { state: "over"; over: number };

export function countCodePoints(text: string): number {
  let count = 0;
  for (const _ of text) count += 1;
  return count;
}

export function composerCounter(text: string, limit: number): ComposerCounter {
  const length = countCodePoints(text.trim());
  if (length > limit) return { state: "over", over: length - limit };
  if (length >= COUNTER_FROM) return { state: "left", left: limit - length };
  return { state: "hidden" };
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

export function maxCharsFromDetail(detail: unknown): number | null {
  if (typeof detail !== "object" || detail === null || !("max_chars" in detail)) return null;
  return positiveInteger((detail as { max_chars?: unknown }).max_chars);
}

export function resolveLimit(serverMax: number | null, configMax: unknown): number {
  return serverMax ?? positiveInteger(configMax) ?? DEFAULT_MAX_CHARS;
}

export function restoreDraft(restored: string, draft: string): string {
  return draft.trim() ? `${restored}\n\n${draft}` : restored;
}
