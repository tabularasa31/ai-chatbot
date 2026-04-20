"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChatWidget, type ChatWidgetBelowAssistantContext } from "@/components/ChatWidget";
import { EvalRatingPanel } from "@/components/eval/EvalRatingPanel";
import { evalApiBase, getEvalToken, removeEvalToken } from "@/lib/evalAuth";

/** Dedupe POST /eval/sessions in React Strict Mode (dev double-mount) and parallel effects. */
const evalSessionBootstrapPromises = new Map<string, Promise<string>>();

function evalSessionBootstrapKey(botId: string, token: string): string {
  return `${botId}::${token.slice(0, 48)}`;
}

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

function EvalStateMessage({
  tone,
  title,
  description,
}: {
  tone: "danger" | "neutral";
  title: string;
  description: string;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#0A0A0F] px-4 py-10">
      <div className="absolute left-[-72px] top-[-64px] h-64 w-64 rounded-full bg-[#E879F9]/20 blur-3xl" />
      <div className="absolute bottom-[-96px] right-[-40px] h-72 w-72 rounded-full bg-[#38BDF8]/18 blur-3xl" />
      <div
        className={`relative max-w-xl rounded-xl border px-6 py-7 ${
          tone === "danger"
            ? "border-[#F87171]/30 bg-[#F87171]/10 text-[#F87171]"
            : "border-[#2E2E3E] bg-[#1E1E2E] text-[#FAF5FF]"
        }`}
      >
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="mt-3 text-sm leading-7 opacity-80">{description}</p>
      </div>
    </div>
  );
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
    const path = botId ? `/eval/chat?bot_id=${encodeURIComponent(botId)}` : "/eval/chat";
    const next = encodeURIComponent(path);
    router.replace(`/eval/login?next=${next}`);
  }, [router, botId]);

  const handleEvalAuthFailure = useCallback(() => {
    removeEvalToken();
    redirectToLogin();
  }, [redirectToLogin]);

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

    const key = evalSessionBootstrapKey(botId, token);
    let promise = evalSessionBootstrapPromises.get(key);
    if (!promise) {
      setSessionLoading(true);
      setSessionError("");
      promise = (async () => {
        const res = await fetch(`${apiBase}/eval/sessions`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ bot_id: botId }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) {
          removeEvalToken();
          throw new Error("__eval_auth_401");
        }
        if (!res.ok) {
          throw new Error(formatApiDetail((data as { detail?: unknown }).detail, `Ошибка ${res.status}`));
        }
        const id = (data as { id?: string }).id;
        if (!id) throw new Error("Нет id сессии в ответе");
        return id;
      })().finally(() => {
        evalSessionBootstrapPromises.delete(key);
      });
      evalSessionBootstrapPromises.set(key, promise);
    } else {
      setSessionLoading(true);
      setSessionError("");
    }

    let cancelled = false;
    promise
      .then((id) => {
        if (cancelled) return;
        setEvalSessionId(id);
        setSessionError("");
        setSessionLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setSessionLoading(false);
        setEvalSessionId(null);
        if (e instanceof Error && e.message === "__eval_auth_401") {
          redirectToLogin();
          return;
        }
        if (e instanceof Error) {
          setSessionError(e.message);
        } else {
          setSessionError("Не удалось создать сессию оценки");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [apiBase, botId, redirectToLogin]);

  const markSaved = useCallback((idx: number) => {
    setSavedMessageIndexes((prev) => {
      const next = new Set(prev);
      next.add(idx);
      return next;
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
          onAuthFailed={handleEvalAuthFailure}
        />
      );
    },
    [apiBase, evalSessionId, savedMessageIndexes, markSaved, handleEvalAuthFailure],
  );

  if (!botId) {
    return (
      <EvalStateMessage
        tone="danger"
        title="Нужен bot_id в URL"
        description="Откройте страницу в формате /eval/chat?bot_id=ch_... , чтобы создать eval-сессию для конкретного бота."
      />
    );
  }

  if (sessionLoading && !evalSessionId && !sessionError) {
    return (
      <EvalStateMessage
        tone="neutral"
        title="Подготовка eval-сессии"
        description="Создаю защищённую сессию для ручной проверки ответов. Обычно это занимает пару секунд."
      />
    );
  }

  if (sessionError || !evalSessionId) {
    return (
      <EvalStateMessage
        tone="danger"
        title="Чат временно недоступен"
        description={sessionError || "Сессия оценки не создана. Попробуйте открыть страницу заново."}
      />
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-[#0A0A0F]">
      <header className="border-b border-[#1E1E2E] bg-[#12121A] px-8 py-4">
        <div className="flex items-center justify-between">
          <span className="text-[#E879F9] font-medium cursor-default">
            UI Evals
          </span>
          <h1 className="text-[#FAF5FF] font-medium">Ручная проверка диалога</h1>
          <span className="text-[#FAF5FF]/60 text-sm">{botId}</span>
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center p-8">
        <div className="flex h-[700px] w-full max-w-4xl overflow-hidden rounded-xl border border-[#1E1E2E] shadow-2xl">
          <ChatWidget botId={botId} locale={locale} renderBelowAssistant={renderBelowAssistant} />
        </div>
      </main>
    </div>
  );
}

export default function EvalChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[#0A0A0F] px-4 text-sm text-[#FAF5FF]/60">
          Загрузка...
        </div>
      }
    >
      <EvalChatContent />
    </Suspense>
  );
}
