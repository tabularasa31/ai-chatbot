"use client";

import { useState } from "react";
import { AlertTriangle, Check, MessageSquare, Tag } from "lucide-react";
import { cn } from "@/components/ui/utils";

const CATEGORIES: { value: string; label: string }[] = [
  { value: "hallucination", label: "Галлюцинация" },
  { value: "incomplete", label: "Неполный ответ" },
  { value: "wrong_generation", label: "Неверная интерпретация" },
  { value: "off_topic", label: "Мимо вопроса" },
  { value: "no_answer", label: "Отказ отвечать" },
  { value: "other", label: "Другое" },
];

type Verdict = "pass" | "fail" | null;

type Props = {
  apiBase: string;
  evalSessionId: string;
  messageIndex: number;
  userQuestion: string;
  botAnswer: string;
  saved: boolean;
  onSaved: (messageIndex: number) => void;
  getToken: () => string | null;
  /** Expired / invalid eval token: clear storage and return to login. */
  onAuthFailed?: () => void;
};

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

export function EvalRatingPanel({
  apiBase,
  evalSessionId,
  messageIndex,
  userQuestion,
  botAnswer,
  saved,
  onSaved,
  getToken,
  onAuthFailed,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [verdict, setVerdict] = useState<Verdict>(null);
  const [errorCategory, setErrorCategory] = useState("");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [frozen, setFrozen] = useState<{
    verdict: "pass" | "fail";
    error_category: string | null;
    comment: string | null;
  } | null>(null);
  const normalizedQuestion = userQuestion.trim();
  const normalizedBotAnswer = botAnswer.trim();

  if (!normalizedQuestion) {
    return null;
  }

  if (saved && frozen) {
    return (
      <div className="rounded-lg border border-[#4ADE80]/30 bg-[#4ADE80]/10 px-4 py-3 text-sm text-[#4ADE80]">
        <div className="flex flex-wrap items-center gap-2">
          <Check size={14} />
          <span className="font-semibold">Оценка: {frozen.verdict === "pass" ? "Pass" : "Fail"}</span>
        </div>
        {frozen.verdict === "fail" && frozen.error_category ? (
          <p className="mt-1.5 text-sm opacity-80">
            Категория:{" "}
            {CATEGORIES.find((c) => c.value === frozen.error_category)?.label ?? frozen.error_category}
          </p>
        ) : null}
        {frozen.comment ? (
          <p className="mt-1.5 text-sm opacity-80">Комментарий: {frozen.comment}</p>
        ) : null}
      </div>
    );
  }

  if (saved && !frozen) {
    return null;
  }

  if (!expanded) {
    return (
      <div className="rounded-lg border border-[#2E2E3E] bg-[#1E1E2E] px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-[#FAF5FF]/60">Оценка ответа необязательна. Можно сразу продолжать диалог.</p>
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="inline-flex items-center rounded-lg border border-[#2E2E3E] bg-[#0A0A0F] px-3 py-1.5 text-sm font-medium text-[#FAF5FF]/80 transition-colors hover:border-[#E879F9]/50 hover:text-[#FAF5FF]"
          >
            Оценить ответ
          </button>
        </div>
      </div>
    );
  }

  const canSavePass = verdict === "pass";
  const canSaveFail = verdict === "fail" && errorCategory.trim().length > 0 && comment.trim().length > 0;

  async function handleSave() {
    if (!verdict) return;
    const token = getToken();
    if (!token) {
      setSaveError("Нет токена. Войдите снова.");
      return;
    }

    setSaveError("");
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        question: normalizedQuestion,
        bot_answer: normalizedBotAnswer,
        verdict,
      };
      if (verdict === "fail") {
        if (errorCategory) body.error_category = errorCategory;
        if (comment.trim()) body.comment = comment.trim();
      }

      const res = await fetch(`${apiBase}/eval/sessions/${evalSessionId}/results`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body),
      });
      if (res.status === 401) {
        onAuthFailed?.();
        return;
      }

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          formatApiDetail((data as { detail?: unknown }).detail, `Ошибка ${res.status}`),
        );
      }

      setFrozen({
        verdict,
        error_category: verdict === "fail" && errorCategory ? errorCategory : null,
        comment:
          verdict === "fail" && comment.trim()
            ? comment.trim()
            : verdict === "pass" && comment.trim()
              ? comment.trim()
              : null,
      });
      onSaved(messageIndex);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-[#2E2E3E] bg-[#1E1E2E] p-4">
      {/* Header row */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-sm font-medium text-[#FAF5FF]">Оцените ответ ассистента</h4>
          <p className="mt-1 text-xs text-[#FAF5FF]/50">Это необязательный шаг. Следующий вопрос можно задать в любой момент.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setExpanded(false);
              setVerdict(null);
              setErrorCategory("");
              setComment("");
              setSaveError("");
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#2E2E3E] bg-[#0A0A0F] px-3 py-1.5 text-xs font-medium text-[#FAF5FF]/60 transition-all hover:border-[#FAF5FF]/30 hover:text-[#FAF5FF]/80"
          >
            Позже
          </button>
          <button
            type="button"
            onClick={() => {
              setVerdict(verdict === "pass" ? null : "pass");
              setErrorCategory("");
              setSaveError("");
            }}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border-2 px-3 py-1.5 text-xs font-medium transition-all",
              verdict === "pass"
                ? "border-[#4ADE80] bg-[#4ADE80]/10 text-[#4ADE80]"
                : "border-[#2E2E3E] bg-[#0A0A0F] text-[#FAF5FF]/60 hover:border-[#4ADE80]/50 hover:text-[#4ADE80]",
            )}
          >
            <Check size={13} />
            Pass
          </button>
          <button
            type="button"
            onClick={() => {
              setVerdict(verdict === "fail" ? null : "fail");
              setSaveError("");
            }}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border-2 px-3 py-1.5 text-xs font-medium transition-all",
              verdict === "fail"
                ? "border-[#F87171] bg-[#F87171]/10 text-[#F87171]"
                : "border-[#2E2E3E] bg-[#0A0A0F] text-[#FAF5FF]/60 hover:border-[#F87171]/50 hover:text-[#F87171]",
            )}
          >
            <AlertTriangle size={13} />
            Fail
          </button>
        </div>
      </div>

      {/* Fail details */}
      {verdict === "fail" && (
        <div className="space-y-3 mt-3">
          <div>
            <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[#FAF5FF]/70">
              <Tag size={12} />
              Категория
            </label>
            <select
              value={errorCategory}
              onChange={(e) => {
                setErrorCategory(e.target.value);
                setSaveError("");
              }}
              className="w-full rounded-lg border border-[#2E2E3E] bg-[#0A0A0F] px-3 py-2 text-sm text-[#FAF5FF] outline-none transition focus:border-transparent focus:ring-2 focus:ring-[#E879F9]"
            >
              <option value="">Выберите категорию</option>
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[#FAF5FF]/70">
              <MessageSquare size={12} />
              Комментарий
            </label>
            <textarea
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                setSaveError("");
              }}
              placeholder="Что именно пошло не так?"
              rows={3}
              className="w-full resize-none rounded-lg border border-[#2E2E3E] bg-[#0A0A0F] px-3 py-2 text-sm leading-6 text-[#FAF5FF] outline-none transition placeholder:text-[#FAF5FF]/40 focus:border-transparent focus:ring-2 focus:ring-[#E879F9]"
            />
          </div>
        </div>
      )}

      {saveError && (
        <div className="mt-3 rounded-lg border border-[#F87171]/30 bg-[#F87171]/10 px-3 py-2 text-sm text-[#F87171]">
          {saveError}
        </div>
      )}

      {verdict && (
        <div className="mt-4">
          <button
            type="button"
            disabled={saving || (!canSavePass && !canSaveFail)}
            onClick={handleSave}
            className="rounded-lg bg-[#E879F9] px-4 py-2 text-sm font-medium text-[#0A0A0F] transition-colors hover:bg-[#f099fb] disabled:cursor-not-allowed disabled:bg-[#2E2E3E] disabled:text-[#FAF5FF]/40"
          >
            {saving ? "Сохранение..." : "Сохранить оценку"}
          </button>
        </div>
      )}
    </div>
  );
}
