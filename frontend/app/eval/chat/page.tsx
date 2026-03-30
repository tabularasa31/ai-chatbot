"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Bot, ClipboardCheck } from "lucide-react";
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#F4F7FB] px-4 py-10 font-['Inter']">
      <div className="absolute left-[-72px] top-[-64px] h-64 w-64 rounded-full bg-[#E879F9]/18 blur-3xl" />
      <div className="absolute bottom-[-96px] right-[-40px] h-72 w-72 rounded-full bg-[#38BDF8]/18 blur-3xl" />
      <div
        className={`relative max-w-xl rounded-[30px] border px-6 py-7 shadow-[0_30px_100px_rgba(15,23,42,0.12)] ${
          tone === "danger"
            ? "border-[#FECACA] bg-[#FFF1F2] text-[#991B1B]"
            : "border-[#DCE5F2] bg-white text-[#0F172A]"
        }`}
      >
        <h1 className="text-2xl font-semibold tracking-[-0.03em]">{title}</h1>
        <p className="mt-3 text-sm leading-7 text-inherit/80">{description}</p>
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
    <div className="relative min-h-screen overflow-hidden bg-[#F4F7FB] font-['Inter']">
      <div className="absolute left-[-96px] top-[-80px] h-72 w-72 rounded-full bg-[#E879F9]/18 blur-3xl" />
      <div className="absolute bottom-[-120px] right-[-32px] h-80 w-80 rounded-full bg-[#38BDF8]/16 blur-3xl" />

      <div className="relative mx-auto flex min-h-screen max-w-7xl flex-col px-3 py-3 sm:px-4 sm:py-4">
        <div className="mb-3 rounded-[28px] border border-white/80 bg-white/86 px-4 py-4 shadow-[0_22px_70px_rgba(15,23,42,0.1)] backdrop-blur sm:px-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <div className="inline-flex items-center gap-2 rounded-full border border-[#DBEAFE] bg-[#EEF5FF] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-[#2563EB]">
                <ClipboardCheck size={14} />
                UI Eval
              </div>
              <h1 className="mt-3 text-2xl font-semibold tracking-[-0.04em] text-[#0F172A] sm:text-[2rem]">
                Ручная проверка диалога
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-7 text-[#64748B]">
                Тестируйте ответы в светлом брендированном интерфейсе и сохраняйте оценку прямо под каждой репликой ассистента.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex items-center gap-2 rounded-full border border-[#DCE5F2] bg-white px-3 py-2 text-xs font-medium text-[#475569]">
                <Bot size={14} />
                {botId}
              </div>
              <div className="inline-flex items-center rounded-full bg-[#ECFDF3] px-3 py-2 text-xs font-medium text-[#166534]">
                Eval session active
              </div>
            </div>
          </div>
        </div>

        <div className="flex flex-1 justify-center">
          <div className="flex h-[min(72vh,600px)] w-full max-w-4xl min-h-[520px]">
            <ChatWidget
              clientId={botId}
              locale={locale}
              badge="UI Eval"
              title="Проверка ответов бота"
              subtitle="Светлый интерфейс для QA: проверяйте сценарии, оценивайте ответы и быстро подмечайте слабые места."
              renderBelowAssistant={renderBelowAssistant}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function EvalChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[#F4F7FB] px-4 text-sm text-[#64748B]">
          Загрузка...
        </div>
      }
    >
      <EvalChatContent />
    </Suspense>
  );
}
