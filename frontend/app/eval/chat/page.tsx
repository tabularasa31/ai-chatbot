"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChatWidget, type ChatWidgetBelowAssistantContext } from "@/components/ChatWidget";
import { EvalRatingPanel } from "@/components/eval/EvalRatingPanel";
import { evalApiBase, getEvalToken } from "@/lib/evalAuth";

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first) {
      return String((first as { msg: unknown }).msg);
    }
  }
  return fallback;
}

function EvalChatContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const botId = searchParams.get("bot_id")?.trim() || "";

  const apiBase = evalApiBase();
  const [evalSessionId, setEvalSessionId] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState("");
  const [sessionLoading, setSessionLoading] = useState(false);
  const [savedMessageIndexes, setSavedMessageIndexes] = useState<Set<number>>(() => new Set());
  const [locale, setLocale] = useState<string | null>(null);
  useEffect(() => {
    setLocale(typeof navigator !== "undefined" ? navigator.language : null);
  }, []);

  const redirectToLogin = useCallback(() => {
    const path = botId
      ? `/eval/chat?bot_id=${encodeURIComponent(botId)}`
      : "/eval/chat";
    const next = encodeURIComponent(path);
    router.replace(`/eval/login?next=${next}`);
  }, [router, botId]);

  useEffect(() => {
    const token = getEvalToken();
    if (!token) {
      redirectToLogin();
    }
  }, [redirectToLogin]);

  useEffect(() => {
    if (!botId) return;
    const token = getEvalToken();
    if (!token) return;

    let cancelled = false;
    setSessionLoading(true);
    setSessionError("");

    (async () => {
      try {
        const res = await fetch(`${apiBase}/eval/sessions`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ bot_id: botId }),
        });
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setSessionError(
            formatApiDetail((data as { detail?: unknown }).detail, `Ошибка ${res.status}`)
          );
          setEvalSessionId(null);
          return;
        }
        const id = (data as { id?: string }).id;
        if (!id) {
          setSessionError("Нет id сессии в ответе");
          setEvalSessionId(null);
          return;
        }
        setEvalSessionId(id);
      } catch {
        if (!cancelled) {
          setSessionError("Не удалось создать сессию оценки");
          setEvalSessionId(null);
        }
      } finally {
        if (!cancelled) setSessionLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [apiBase, botId]);

  const markSaved = useCallback((idx: number) => {
    setSavedMessageIndexes((prev) => {
      const n = new Set(prev);
      n.add(idx);
      return n;
    });
  }, []);

  const renderBelowAssistant = useCallback(
    function evalRenderBelowAssistant(ctx: ChatWidgetBelowAssistantContext) {
      if (!evalSessionId) return null;
      return (
        <EvalRatingPanel
          apiBase={apiBase}
          evalSessionId={evalSessionId}
          messageIndex={ctx.messageIndex}
          userQuestion={ctx.userQuestion}
          botAnswer={ctx.assistantContent}
          saved={savedMessageIndexes.has(ctx.messageIndex)}
          onSaved={markSaved}
          getToken={getEvalToken}
        />
      );
    },
    [apiBase, evalSessionId, savedMessageIndexes, markSaved],
  );

  if (!botId) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "24px",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: "420px",
            padding: "20px",
            borderRadius: "12px",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#991b1b",
            fontSize: "14px",
          }}
        >
          Укажите <code style={{ background: "#fee2e2", padding: "2px 6px", borderRadius: "4px" }}>bot_id</code> в
          URL, например <code style={{ background: "#fee2e2", padding: "2px 6px", borderRadius: "4px" }}>/eval/chat?bot_id=ch_…</code>
        </div>
      </div>
    );
  }

  if (sessionLoading && !evalSessionId && !sessionError) {
    return (
      <div style={{ padding: "40px", textAlign: "center", fontFamily: "system-ui, sans-serif" }}>
        Подготовка сессии оценки…
      </div>
    );
  }

  if (sessionError || !evalSessionId) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "24px",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: "480px",
            padding: "20px",
            borderRadius: "12px",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#991b1b",
            fontSize: "14px",
          }}
        >
          {sessionError || "Сессия оценки не создана. Чат отключён."}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: "#fff",
      }}
    >
      <ChatWidget clientId={botId} locale={locale} renderBelowAssistant={renderBelowAssistant} />
    </div>
  );
}

export default function EvalChatPage() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: "40px", textAlign: "center", fontFamily: "system-ui, sans-serif" }}>
          Загрузка…
        </div>
      }
    >
      <EvalChatContent />
    </Suspense>
  );
}
