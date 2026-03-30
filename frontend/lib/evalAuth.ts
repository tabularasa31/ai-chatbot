const EVAL_TOKEN_KEY = "chat9_eval_access_token";
// 24 hours — matches EVAL JWT expiry in backend/eval/tokens.py
const EVAL_COOKIE_MAX_AGE = 86400;

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

export function getEvalToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(EVAL_TOKEN_KEY);
}

export function saveEvalToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(EVAL_TOKEN_KEY, token);
  document.cookie = `${EVAL_TOKEN_KEY}=${token}; path=/eval; max-age=${EVAL_COOKIE_MAX_AGE}; samesite=strict`;
}

export function removeEvalToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(EVAL_TOKEN_KEY);
  document.cookie = `${EVAL_TOKEN_KEY}=; path=/eval; max-age=0; samesite=strict`;
}

/** Allow only same-origin eval paths as redirect target after login. */
export function safeEvalNext(next: string | null): string {
  if (!next || !next.startsWith("/")) return "/eval/chat";
  if (!next.startsWith("/eval/")) return "/eval/chat";
  return next;
}

export function evalApiBase(): string {
  return BASE_URL;
}
