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

/**
 * Fetches the welcome text for a browser that has not been greeted yet and
 * records that it was. Resolves to null when already greeted or when the
 * request fails; a failure leaves the marker unset so a later visit retries.
 * The request is a read: it never creates a session or a conversation.
 */
export async function greetOnce({
  apiBase,
  botId,
  userId,
  locale,
  fetchImpl = fetch,
}: {
  apiBase: string;
  botId: string;
  userId?: string | null;
  locale?: string;
  fetchImpl?: typeof fetch;
}): Promise<string | null> {
  if (hasBeenGreeted(botId, userId)) return null;
  const params = new URLSearchParams({ bot_id: botId });
  if (locale) params.set("locale", locale);
  try {
    const res = await fetchImpl(`${apiBase}/widget/greeting?${params}`);
    if (!res.ok) throw new Error(`Greeting error: ${res.status}`);
    const { text } = (await res.json()) as { text?: unknown };
    if (typeof text !== "string" || !text.trim()) throw new Error("Greeting response has no text");
    markGreeted(botId, userId);
    return text;
  } catch (error) {
    console.error("Chat9: greeting failed", error);
    return null;
  }
}
