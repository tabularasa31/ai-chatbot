"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ShieldCheck, Sparkles } from "lucide-react";
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#F4F7FB] px-4 py-10 font-['Inter']">
      <div className="absolute left-[-96px] top-[-88px] h-72 w-72 rounded-full bg-[#E879F9]/20 blur-3xl" />
      <div className="absolute bottom-[-120px] right-[-48px] h-80 w-80 rounded-full bg-[#38BDF8]/18 blur-3xl" />

      <div className="relative w-full max-w-5xl overflow-hidden rounded-[34px] border border-white/80 bg-white/88 shadow-[0_38px_120px_rgba(15,23,42,0.14)] backdrop-blur">
        <div className="grid lg:grid-cols-[0.96fr_1.04fr]">
          <section className="relative overflow-hidden border-b border-[#E7EEF8] bg-[linear-gradient(180deg,#0F172A_0%,#111827_100%)] px-6 py-8 text-white lg:border-b-0 lg:border-r lg:border-r-white/10 lg:px-8 lg:py-10">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(232,121,249,0.28),transparent_34%),radial-gradient(circle_at_bottom_right,rgba(56,189,248,0.26),transparent_34%)]" />
            <div className="relative">
              <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.24em] text-[#BFDBFE]">
                <Sparkles size={14} />
                Chat9 Eval
              </span>
              <h1 className="mt-6 max-w-sm text-4xl font-semibold tracking-[-0.04em] text-[#F8FAFC] sm:text-[2.8rem]">
                Вход тестировщика
              </h1>
              <p className="mt-4 max-w-md text-sm leading-7 text-[#CBD5E1] sm:text-base">
                Внутренняя UI-оценка ботов: тестируйте сценарии, проверяйте ответы и фиксируйте качество прямо в диалоге.
              </p>

              <div className="mt-8 grid gap-3">
                <div className="rounded-[24px] border border-white/10 bg-white/8 px-4 py-4">
                  <p className="text-xs uppercase tracking-[0.22em] text-[#93C5FD]">Для QA-команды</p>
                  <p className="mt-2 text-sm leading-6 text-[#E2E8F0]">
                    Быстрый доступ к eval-сессиям без лишних экранов и с оценкой каждого ответа в один клик.
                  </p>
                </div>
                <div className="rounded-[24px] border border-white/10 bg-white/8 px-4 py-4">
                  <p className="text-xs uppercase tracking-[0.22em] text-[#F5D0FE]">Светлый диалог</p>
                  <p className="mt-2 text-sm leading-6 text-[#E2E8F0]">
                    Бот остаётся на светлом фоне, но получает более выразительные акценты, контраст и иерархию.
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section className="px-6 py-8 sm:px-8 sm:py-10">
            <div className="inline-flex items-center gap-2 rounded-full border border-[#DBEAFE] bg-[#EEF5FF] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-[#2563EB]">
              <ShieldCheck size={14} />
              Secure access
            </div>
            <h2 className="mt-5 text-3xl font-semibold tracking-[-0.03em] text-[#0F172A]">
              Войдите в eval-интерфейс
            </h2>
            <p className="mt-3 max-w-md text-sm leading-7 text-[#64748B]">
              Используйте тестовые учётные данные, чтобы открыть сценарий проверки и сохранить оценки в защищённой сессии.
            </p>

            {error ? (
              <div className="mt-6 rounded-[22px] border border-[#FECACA] bg-[#FFF1F2] px-4 py-3 text-sm text-[#B91C1C]">
                {error}
              </div>
            ) : null}

            <form onSubmit={handleSubmit} className="mt-8 space-y-5">
              <div className="space-y-2">
                <label htmlFor="eval-username" className="block text-sm font-semibold text-[#334155]">
                  Имя пользователя
                </label>
                <input
                  id="eval-username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  placeholder="elina"
                  className="w-full rounded-[22px] border border-[#D6E1F0] bg-white px-4 py-4 text-base text-[#0F172A] shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] outline-none transition focus:border-[#60A5FA] focus:ring-4 focus:ring-[#DBEAFE]"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="eval-password" className="block text-sm font-semibold text-[#334155]">
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
                  className="w-full rounded-[22px] border border-[#D6E1F0] bg-white px-4 py-4 text-base text-[#0F172A] shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] outline-none transition focus:border-[#60A5FA] focus:ring-4 focus:ring-[#DBEAFE]"
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="inline-flex w-full items-center justify-center rounded-[22px] bg-[linear-gradient(135deg,#0F172A_0%,#2563EB_45%,#E879F9_100%)] px-5 py-4 text-base font-semibold text-white shadow-[0_26px_56px_rgba(37,99,235,0.28)] transition hover:-translate-y-0.5 hover:shadow-[0_30px_64px_rgba(37,99,235,0.34)] disabled:translate-y-0 disabled:cursor-not-allowed disabled:bg-[#CBD5E1] disabled:shadow-none"
              >
                {loading ? "Вход..." : "Войти"}
              </button>
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}

export default function EvalLoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[#F4F7FB] px-4 text-sm text-[#64748B]">
          Загрузка...
        </div>
      }
    >
      <EvalLoginForm />
    </Suspense>
  );
}
