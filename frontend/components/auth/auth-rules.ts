import { flushSync } from "react-dom";
import { ApiError } from "@/lib/api";

const PASSWORD_PATTERN = /^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=[\]{};':"\\|,.<>\/?]).{8,}$/;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_HELPER = "At least 8 characters, with an uppercase letter, a number and a special character.";
export const PASSWORD_RULE_ERROR =
  "Use at least 8 characters, with an uppercase letter, a number and a special character.";

const CONNECTION_ERROR = "Can't reach Chat9. Check your connection and try again.";
const RATE_LIMIT_ERROR = "Too many attempts. Wait a while and try again.";
const SERVER_ERROR = "Something went wrong on our side. Try again in a moment.";

export type FieldErrors<K extends string> = Record<K, string | null>;

export function emailError(email: string): string | null {
  if (!email) return "Enter your email.";
  return EMAIL_PATTERN.test(email) ? null : "Enter a valid email address.";
}

export function newPasswordError(password: string, emptyMessage: string): string | null {
  if (!password) return emptyMessage;
  return PASSWORD_PATTERN.test(password) ? null : PASSWORD_RULE_ERROR;
}

export function hasErrors(errors: Record<string, string | null>): boolean {
  return Object.values(errors).some(Boolean);
}

export function focusField(id: string): void {
  document.getElementById(id)?.focus();
}

/** Commits `errors` before moving focus, so the field is already marked invalid when it is announced. Field ids are the keys, in form order. */
export function applyFieldErrors<E extends Record<string, string | null>>(setErrors: (errors: E) => void, errors: E): void {
  flushSync(() => setErrors(errors));
  const id = Object.keys(errors).find((key) => errors[key]);
  if (id) focusField(id);
}

export function formErrorMessage(err: unknown): string {
  if (err instanceof TypeError) return CONNECTION_ERROR;
  if (err instanceof ApiError && err.status === 429) return RATE_LIMIT_ERROR;
  return SERVER_ERROR;
}

export function isApiStatus(err: unknown, status: number): boolean {
  return err instanceof ApiError && err.status === status;
}
