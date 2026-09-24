import type { UserHints } from "@chat9/widget-shared";

const SESSION_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;

// Storage-key discriminator derived from hints. Lets us namespace localStorage
// per visitor so one browser shared between accounts doesn't bleed history
// across them. Falls back to email-based synthetic id matching the backend.
export function deriveStorageUserId(hints: UserHints | null | undefined): string | null {
  if (!hints) return null;
  if (hints.user_id && hints.user_id.trim()) return hints.user_id.trim();
  if (hints.email && hints.email.trim()) return `hint:${hints.email.trim()}`;
  return null;
}

function sessionStorageKey(botId: string, userId?: string | null): string {
  return userId ? `chat9:${botId}:${userId}:session` : `chat9:${botId}:session`;
}

function sessionUpdatedAtStorageKey(botId: string, userId?: string | null): string {
  return userId ? `chat9:${botId}:${userId}:session_updated_at` : `chat9:${botId}:session_updated_at`;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function clearStoredSession(botId: string, userId?: string | null): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(sessionStorageKey(botId, userId));
    window.localStorage.removeItem(sessionUpdatedAtStorageKey(botId, userId));
  } catch {
    // localStorage can be blocked in embedded/privacy-restricted contexts.
  }
}

export function readStoredSession(botId: string, userId?: string | null): string | null {
  if (typeof window === "undefined") return null;
  let storedSessionId: string | null = null;
  let storedUpdatedAt: string | null = null;
  try {
    storedSessionId = window.localStorage.getItem(sessionStorageKey(botId, userId));
    storedUpdatedAt = window.localStorage.getItem(sessionUpdatedAtStorageKey(botId, userId));
  } catch {
    return null;
  }
  if (!storedSessionId || !storedUpdatedAt) {
    clearStoredSession(botId, userId);
    return null;
  }
  if (!isUuid(storedSessionId)) {
    clearStoredSession(botId, userId);
    return null;
  }
  const updatedAtMs = Number(storedUpdatedAt);
  if (!Number.isFinite(updatedAtMs) || Date.now() - updatedAtMs > SESSION_STORAGE_TTL_MS) {
    clearStoredSession(botId, userId);
    return null;
  }
  return storedSessionId;
}

// The sliding 24h TTL is the lifetime of the visitor identity, not of a
// conversation: the server rotates conversations on its own idle timeout and
// keeps them linked to this session_id.
export function persistSession(botId: string, sessionId: string, userId?: string | null): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(sessionStorageKey(botId, userId), sessionId);
    window.localStorage.setItem(sessionUpdatedAtStorageKey(botId, userId), String(Date.now()));
  } catch {
    // Persistence is best-effort; widget can continue without browser storage.
  }
}
