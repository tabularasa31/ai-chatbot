import type { LlmFailureState, WidgetSource, WidgetTurnPayload } from "@chat9/widget-shared";

export function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first) {
      return String((first as { msg: unknown }).msg);
    }
  }
  return fallback;
}

export function apiErrorCode(detail: unknown): string | null {
  if (typeof detail === "object" && detail !== null && "code" in detail) {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === "string" && code.trim()) return code;
  }
  return null;
}

export async function requestWidgetTurn({
  apiBase,
  botId,
  locale,
  message,
  attemptSessionId,
  onChunk,
  onStatus,
}: {
  apiBase: string;
  botId: string;
  locale: string | undefined;
  message: string;
  attemptSessionId: string | null;
  onChunk?: (partialText: string) => void;
  onStatus?: (stage: string) => void;
}): Promise<{ res: Response; payload: WidgetTurnPayload }> {
  const params = new URLSearchParams({
    bot_id: botId,
  });
  if (attemptSessionId) params.set("session_id", attemptSessionId);

  const res = await fetch(`${apiBase}/widget/chat?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      locale,
    }),
  });

  if (!res.ok || !res.body) {
    const payload = (await res.json().catch(() => ({}))) as WidgetTurnPayload;
    return { res, payload };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";
  const payload: WidgetTurnPayload = {};

  const handleEvent = (eventData: string) => {
    const raw = eventData.trim();
    if (!raw) return;
    let parsed: {
      type?: string;
      text?: string;
      stage?: string;
      session_id?: string;
      ticket_number?: string;
      message?: string;
      code?: number;
      sources?: WidgetSource[];
      outcome?: string | null;
      failure_state?: LlmFailureState | null;
    };
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }
    if (parsed.type === "chunk" && typeof parsed.text === "string") {
      fullText += parsed.text;
      onChunk?.(fullText);
    } else if (parsed.type === "status" && typeof parsed.stage === "string") {
      onStatus?.(parsed.stage);
    } else if (parsed.type === "done") {
      payload.text = typeof parsed.text === "string" ? parsed.text : fullText;
      payload.session_id = parsed.session_id;
      payload.ticket_number = parsed.ticket_number ?? null;
      payload.sources = parsed.sources ?? [];
      payload.outcome = parsed.outcome ?? null;
      payload.failure_state = parsed.failure_state ?? null;
      // Don't replay the final text into onChunk for the degraded path:
      // the LLM-unavailable message is rendered as its own UI block, not
      // as an assistant bubble streamed token-by-token.
      if (
        parsed.outcome !== "llm_unavailable" &&
        typeof parsed.text === "string" &&
        parsed.text !== fullText
      ) {
        onChunk?.(parsed.text);
      }
    } else if (parsed.type === "error") {
      payload.detail = {
        code: parsed.code,
        message: parsed.message ?? "stream_error",
      };
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const dataLines = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (dataLines) handleEvent(dataLines);
      }
    }
    if (done) break;
  }

  if (payload.detail !== undefined) {
    throw new Error(formatApiDetail(payload.detail, "Stream error"));
  }

  return { res, payload };
}
