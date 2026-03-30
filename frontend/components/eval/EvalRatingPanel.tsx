"use client";

import { useState } from "react";
import { AlertTriangle, CheckCheck, Flag, MessageSquareText } from "lucide-react";
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

  if (saved && frozen) {
    return (
      <div className="rounded-[22px] border border-[#BBF7D0] bg-[linear-gradient(180deg,#F7FEF8_0%,#ECFDF3_100%)] px-4 py-3 text-sm text-[#166534] shadow-[0_16px_34px_rgba(34,197,94,0.1)]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">Оценка: {frozen.verdict === "pass" ? "Pass" : "Fail"}</span>
        </div>
        {frozen.verdict === "fail" && frozen.error_category ? (
          <p className="mt-2 text-sm text-[#166534]">
            Категория:{" "}
            {CATEGORIES.find((c) => c.value === frozen.error_category)?.label ?? frozen.error_category}
          </p>
        ) : null}
        {frozen.comment ? (
          <p className="mt-2 text-sm leading-6 text-[#166534]">Комментарий: {frozen.comment}</p>
        ) : null}
      </div>
    );
  }

  if (saved && !frozen) {
    return null;
  }

  const canSavePass = verdict === "pass";
  const canSaveFail = verdict === "fail" && (errorCategory !== "other" || comment.trim().length > 0);

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
        question: userQuestion,
        bot_answer: botAnswer,
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
    <div className="rounded-[22px] border border-[#DCE5F2] bg-[linear-gradient(180deg,rgba(255,255,255,0.98)_0%,rgba(248,251,255,0.98)_100%)] px-4 py-4 shadow-[0_18px_40px_rgba(148,163,184,0.12)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="mt-1 text-sm font-semibold text-[#0F172A]">Оцените ответ ассистента</h4>
        </div>
        <div className="inline-flex rounded-full border border-[#DCE5F2] bg-white p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.95)]">
          <button
            type="button"
            onClick={() => {
              setVerdict("pass");
              setErrorCategory("");
              setSaveError("");
            }}
            className={cn(
              "inline-flex items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold transition",
              verdict === "pass"
                ? "bg-[#DCFCE7] text-[#166534] shadow-sm"
                : "text-[#64748B] hover:bg-[#F8FAFC] hover:text-[#0F172A]",
            )}
          >
            <CheckCheck size={14} />
            Pass
          </button>
          <button
            type="button"
            onClick={() => {
              setVerdict("fail");
              setSaveError("");
            }}
            className={cn(
              "inline-flex items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold transition",
              verdict === "fail"
                ? "bg-[#FEE2E2] text-[#B91C1C] shadow-sm"
                : "text-[#64748B] hover:bg-[#F8FAFC] hover:text-[#0F172A]",
            )}
          >
            <AlertTriangle size={14} />
            Fail
          </button>
        </div>
      </div>

      {verdict === "fail" ? (
        <div className="mt-4 grid gap-3 rounded-[18px] border border-[#E2E8F0] bg-white/90 p-4">
          <div>
            <label className="mb-2 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-[#64748B]">
              <Flag size={13} />
              Категория
            </label>
            <select
              value={errorCategory}
              onChange={(e) => {
                setErrorCategory(e.target.value);
                setSaveError("");
              }}
              className="w-full rounded-2xl border border-[#D6E1F0] bg-white px-4 py-3 text-sm text-[#0F172A] outline-none transition focus:border-[#60A5FA] focus:ring-4 focus:ring-[#DBEAFE]"
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
            <label className="mb-2 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-[#64748B]">
              <MessageSquareText size={13} />
              Комментарий
            </label>
            <textarea
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                setSaveError("");
              }}
              placeholder={
                errorCategory === "other"
                  ? "Комментарий обязателен для «Другое»"
                  : "Что именно пошло не так?"
              }
              rows={3}
              className="w-full rounded-2xl border border-[#D6E1F0] bg-white px-4 py-3 text-sm leading-6 text-[#0F172A] outline-none transition focus:border-[#60A5FA] focus:ring-4 focus:ring-[#DBEAFE]"
            />
          </div>
        </div>
      ) : null}

      {saveError ? (
        <div className="mt-3 rounded-2xl border border-[#FECACA] bg-[#FFF1F2] px-4 py-3 text-sm text-[#B91C1C]">
          {saveError}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          disabled={saving || (!canSavePass && !canSaveFail)}
          onClick={handleSave}
          className="inline-flex items-center justify-center rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? "Сохранение..." : "Сохранить оценку"}
        </button>
      </div>
    </div>
  );
}
