const EVAL_TOKEN_KEY = "chat9_eval_access_token";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

export function getEvalToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(EVAL_TOKEN_KEY);
}

export function saveEvalToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(EVAL_TOKEN_KEY, token);
}

export function removeEvalToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(EVAL_TOKEN_KEY);
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
