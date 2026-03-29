"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { evalApiBase, safeEvalNext, saveEvalToken } from "@/lib/evalAuth";

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

function EvalLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const apiBase = evalApiBase();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/eval/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          formatApiDetail((data as { detail?: unknown }).detail, "Неверные учётные данные")
        );
      }
      const token = (data as { access_token?: string }).access_token;
      if (!token) throw new Error("Нет токена в ответе");
      saveEvalToken(token);
      const next = safeEvalNext(searchParams.get("next"));
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f9fafb",
        fontFamily: "system-ui, sans-serif",
        padding: "24px",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "400px",
          background: "#fff",
          borderRadius: "12px",
          border: "1px solid #e5e7eb",
          padding: "28px",
          boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
        }}
      >
        <h1 style={{ fontSize: "20px", fontWeight: 600, marginBottom: "8px" }}>
          Вход тестировщика
        </h1>
        <p style={{ fontSize: "13px", color: "#6b7280", marginBottom: "20px" }}>
          Внутренняя оценка ботов (eval)
        </p>
        {error ? (
          <div
            style={{
              marginBottom: "16px",
              padding: "10px 12px",
              borderRadius: "8px",
              background: "#fef2f2",
              border: "1px solid #fecaca",
              color: "#b91c1c",
              fontSize: "13px",
            }}
          >
            {error}
          </div>
        ) : null}
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div>
            <label
              htmlFor="eval-username"
              style={{ display: "block", fontSize: "13px", fontWeight: 500, marginBottom: "6px" }}
            >
              Имя пользователя
            </label>
            <input
              id="eval-username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: "8px",
                border: "1px solid #d1d5db",
                fontSize: "14px",
              }}
            />
          </div>
          <div>
            <label
              htmlFor="eval-password"
              style={{ display: "block", fontSize: "13px", fontWeight: 500, marginBottom: "6px" }}
            >
              Пароль
            </label>
            <input
              id="eval-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: "8px",
                border: "1px solid #d1d5db",
                fontSize: "14px",
              }}
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            style={{
              marginTop: "8px",
              padding: "12px",
              borderRadius: "8px",
              border: "none",
              background: loading ? "#9ca3af" : "#2563eb",
              color: "#fff",
              fontSize: "15px",
              fontWeight: 500,
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "Вход…" : "Войти"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function EvalLoginPage() {
  return (
    <Suspense
      fallback={
        <div style={{ padding: "40px", textAlign: "center", fontFamily: "system-ui, sans-serif" }}>
          Загрузка…
        </div>
      }
    >
      <EvalLoginForm />
    </Suspense>
  );
}
