export type Banner =
  | { kind: "rate_limit" }
  | { kind: "history" }
  | { kind: "escalate"; messageId: string }
  | { kind: "resend"; messageId: string };

export type SendFailures = {
  failedIds: readonly string[];
  banner: Banner | null;
};

export const NO_FAILURES: SendFailures = { failedIds: [], banner: null };

export function classifySendFailure(status: number | null): "too_long" | "rate_limit" | "other" {
  if (status === 413) return "too_long";
  if (status === 429) return "rate_limit";
  return "other";
}

export function showBanner(state: SendFailures, banner: Banner): SendFailures {
  return { ...state, banner };
}

export function dismissBanner(state: SendFailures): SendFailures {
  return state.banner ? { ...state, banner: null } : state;
}

export function dismissBannerOnEdit(state: SendFailures): SendFailures {
  const kind = state.banner?.kind;
  return kind === "rate_limit" || kind === "resend" ? { ...state, banner: null } : state;
}

export function moveToEnd<T extends { id: string }>(items: readonly T[], id: string): T[] {
  const moved = items.find((item) => item.id === id);
  return moved ? [...items.filter((item) => item.id !== id), moved] : [...items];
}

const RECENT_USER_MESSAGES = 3;

type Bubble = { id: string; type?: string; text?: string };

export function keepUnsent<T extends Bubble>(
  hydrated: readonly T[],
  current: readonly T[],
  failedIds: readonly string[],
): T[] {
  const saved = hydrated
    .filter((item) => item.type === "user")
    .slice(-RECENT_USER_MESSAGES)
    .map((item) => item.text?.trim());
  const unsent = current.filter(
    (item) => failedIds.includes(item.id) && !saved.includes(item.text?.trim()),
  );
  return [...hydrated, ...unsent];
}

export function markSendFailed(state: SendFailures, bubbleId: string, cause: "rate_limit" | "other"): SendFailures {
  const failedIds = state.failedIds.includes(bubbleId) ? state.failedIds : [...state.failedIds, bubbleId];
  return cause === "rate_limit" ? { failedIds, banner: { kind: "rate_limit" } } : { ...state, failedIds };
}

export function clearSendFailure(state: SendFailures, bubbleId: string | null): SendFailures {
  return {
    failedIds: bubbleId ? state.failedIds.filter((id) => id !== bubbleId) : state.failedIds,
    banner: null,
  };
}
