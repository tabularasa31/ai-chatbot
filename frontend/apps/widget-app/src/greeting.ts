const GREETED_VALUE = "1";

function greetedStorageKey(botId: string, userId?: string | null): string {
  return userId ? `chat9:${botId}:${userId}:greeted` : `chat9:${botId}:greeted`;
}

export function hasBeenGreeted(botId: string, userId?: string | null): boolean {
  try {
    return window.localStorage.getItem(greetedStorageKey(botId, userId)) === GREETED_VALUE;
  } catch {
    return false;
  }
}

export function markGreeted(botId: string, userId?: string | null): void {
  try {
    window.localStorage.setItem(greetedStorageKey(botId, userId), GREETED_VALUE);
  } catch {
    // Blocked storage only means the visitor may be greeted again next time.
  }
}

/** The tenant-supplied locale hint wins over the browser locale. */
export function greetingLocale(
  hintLocale: string | null | undefined,
  browserLocale: string | undefined,
): string | undefined {
  return hintLocale?.trim() || browserLocale;
}

function clearGreeted(botId: string, userId?: string | null): void {
  try {
    window.localStorage.removeItem(greetedStorageKey(botId, userId));
  } catch {
    // Nothing was stored, so there is nothing to clear.
  }
}

function hasText(body: unknown): body is { text: string } {
  return typeof body === "object" && body !== null && "text" in body
    && typeof body.text === "string" && body.text.trim() !== "";
}

/**
 * Fetches the welcome text for a browser that has not been greeted yet.
 * The marker is set before the request so a second tab opened meanwhile does
 * not greet too, and cleared again when the request fails. Resolves to null
 * when already greeted or on failure. The request is a read: it never creates
 * a session or a conversation.
 */
export async function greetOnce({
  apiBase,
  botId,
  userId,
  locale,
}: {
  apiBase: string;
  botId: string;
  userId?: string | null;
  locale?: string;
}): Promise<string | null> {
  if (hasBeenGreeted(botId, userId)) return null;
  markGreeted(botId, userId);
  const params = new URLSearchParams({ bot_id: botId });
  if (locale) params.set("locale", locale);
  try {
    const res = await fetch(`${apiBase}/widget/greeting?${params}`);
    if (!res.ok) throw new Error(`Greeting error: ${res.status}`);
    const body: unknown = await res.json();
    if (!hasText(body)) throw new Error("Greeting response has no text");
    return body.text;
  } catch (error) {
    clearGreeted(botId, userId);
    console.error("Chat9: greeting failed", error);
    return null;
  }
}
