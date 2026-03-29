"use client";

import { useState } from "react";

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
}: Props) {
  const [verdict, setVerdict] = useState<Verdict>(null);
  const [errorCategory, setErrorCategory] = useState<string>("");
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
      <div
        style={{
          marginTop: "8px",
          padding: "10px 12px",
          borderRadius: "8px",
          background: "#f0fdf4",
          border: "1px solid #86efac",
          fontSize: "12px",
          color: "#14532d",
        }}
      >
        <div>
          Оценка: <strong>{frozen.verdict === "pass" ? "Pass" : "Fail"}</strong>
        </div>
        {frozen.verdict === "fail" && frozen.error_category && (
          <div style={{ marginTop: "4px" }}>
            Категория:{" "}
            {CATEGORIES.find((c) => c.value === frozen.error_category)?.label ??
              frozen.error_category}
          </div>
        )}
        {frozen.comment ? (
          <div style={{ marginTop: "4px" }}>Комментарий: {frozen.comment}</div>
        ) : null}
      </div>
    );
  }

  if (saved && !frozen) {
    return null;
  }

  const canSavePass = verdict === "pass";
  const canSaveFail =
    verdict === "fail" &&
    (errorCategory !== "other" || comment.trim().length > 0);

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
      const res = await fetch(
        `${apiBase}/eval/sessions/${evalSessionId}/results`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          formatApiDetail(
            (data as { detail?: unknown }).detail,
            `Ошибка ${res.status}`
          )
        );
      }
      setFrozen({
        verdict,
        error_category:
          verdict === "fail" && errorCategory ? errorCategory : null,
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
    <div
      style={{
        marginTop: "8px",
        padding: "10px 12px",
        borderRadius: "8px",
        border: "1px solid #e5e7eb",
        background: "#fff",
        fontSize: "13px",
      }}
    >
      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", marginBottom: "8px" }}>
        <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
          <input
            type="radio"
            name={`verdict-${messageIndex}`}
            checked={verdict === "pass"}
            onChange={() => {
              setVerdict("pass");
              setErrorCategory("");
              setSaveError("");
            }}
          />
          Pass
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
          <input
            type="radio"
            name={`verdict-${messageIndex}`}
            checked={verdict === "fail"}
            onChange={() => {
              setVerdict("fail");
              setSaveError("");
            }}
          />
          Fail
        </label>
      </div>

      {verdict === "fail" && (
        <div style={{ marginBottom: "8px" }}>
          <div style={{ fontSize: "12px", color: "#6b7280", marginBottom: "6px" }}>
            Категория (необязательно)
          </div>
          <select
            value={errorCategory}
            onChange={(e) => {
              setErrorCategory(e.target.value);
              setSaveError("");
            }}
            style={{
              width: "100%",
              maxWidth: "280px",
              padding: "6px 8px",
              borderRadius: "6px",
              border: "1px solid #d1d5db",
              fontSize: "13px",
            }}
          >
            <option value="">—</option>
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
          <textarea
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              setSaveError("");
            }}
            placeholder={
              errorCategory === "other"
                ? "Комментарий обязателен для «Другое»"
                : "Комментарий (необязательно)"
            }
            rows={2}
            style={{
              width: "100%",
              marginTop: "8px",
              padding: "8px",
              borderRadius: "6px",
              border: "1px solid #d1d5db",
              fontSize: "13px",
              resize: "vertical",
            }}
          />
        </div>
      )}

      {saveError ? (
        <div style={{ color: "#b91c1c", fontSize: "12px", marginBottom: "8px" }}>{saveError}</div>
      ) : null}

      <button
        type="button"
        disabled={saving || (!canSavePass && !canSaveFail)}
        onClick={handleSave}
        style={{
          padding: "6px 14px",
          borderRadius: "6px",
          border: "none",
          background:
            canSavePass || canSaveFail ? "#2563eb" : "#9ca3af",
          color: "#fff",
          fontSize: "13px",
          cursor: canSavePass || canSaveFail ? "pointer" : "not-allowed",
        }}
      >
        {saving ? "Сохранение…" : "Save"}
      </button>
    </div>
  );
}
