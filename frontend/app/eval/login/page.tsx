"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ShieldCheck, Sparkles } from "lucide-react";
import { evalApiBase, safeEvalNext, saveEvalToken } from "@/lib/evalAuth";
import { AuthCard, authStyles } from "@/components/auth/AuthCard";

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
          formatApiDetail((data as { detail?: unknown }).detail, "Неверные учётные данные"),
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
    <AuthCard>
      <div className="flex items-center gap-2 mb-6">
        <span className="inline-flex items-center gap-2 rounded-full border border-nd-accent/30 bg-nd-accent/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-nd-accent">
          <Sparkles size={12} />
          Chat9 Eval
        </span>
      </div>

      <h1 className={authStyles.headingSm}>Вход тестировщика</h1>
      <p className={authStyles.subtext}>
        Внутренняя UI-оценка ботов: тестируйте сценарии, проверяйте ответы и фиксируйте качество.
      </p>

      <div className="mb-6 rounded-lg border border-nd-border bg-nd-base/50 p-4">
        <div className="flex items-center gap-2 mb-2">
          <ShieldCheck size={14} className="text-nd-info" />
          <span className="text-xs uppercase tracking-[0.2em] text-nd-info">Для QA-команды</span>
        </div>
        <p className="text-sm text-nd-text/70 leading-relaxed">
          Быстрый доступ к eval-сессиям без лишних экранов и с оценкой каждого ответа в один клик.
        </p>
      </div>

      {error && <div className={`${authStyles.error} mb-4`}>{error}</div>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="eval-username" className={authStyles.label}>
            Имя пользователя
          </label>
          <input
            id="eval-username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            placeholder="elina"
            className={authStyles.input}
          />
        </div>

        <div>
          <label htmlFor="eval-password" className={authStyles.label}>
            Пароль
          </label>
          <input
            id="eval-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            placeholder="Введите пароль"
            className={authStyles.input}
          />
        </div>

        <button type="submit" disabled={loading} className={authStyles.button}>
          {loading ? "Вход..." : "Войти"}
        </button>
      </form>
    </AuthCard>
  );
}

export default function EvalLoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-nd-base px-4 text-sm text-nd-text/60">
          Загрузка...
        </div>
      }
    >
      <EvalLoginForm />
    </Suspense>
  );
}
