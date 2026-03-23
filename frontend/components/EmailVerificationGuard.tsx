"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "@/lib/api";

type VerificationState = "loading" | "verified" | "unverified";

const VerificationContext = createContext<VerificationState>("loading");

export function useVerification() {
  return useContext(VerificationContext);
}

export function EmailVerificationGuard({ children }: { children: ReactNode }) {
  const [state, setState] = useState<VerificationState>("loading");
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    api.clients
      .getMe()
      .then(() => setState("verified"))
      .catch(async (err) => {
        const msg = err instanceof Error ? err.message : "";
        if (msg.toLowerCase().includes("email not verified") || msg.includes("403")) {
          try {
            const user = await api.auth.getMe();
            setEmail(user.email);
          } catch {}
          setState("unverified");
        } else {
          // client not found — попробуем создать (первый вход)
          try {
            await api.clients.create("My Workspace");
            setState("verified");
          } catch (createErr) {
            const createMsg = createErr instanceof Error ? createErr.message : "";
            if (
              createMsg.toLowerCase().includes("email not verified") ||
              createMsg.includes("403")
            ) {
              try {
                const user = await api.auth.getMe();
                setEmail(user.email);
              } catch {}
              setState("unverified");
            } else if (createMsg.includes("already exists") || createMsg.includes("409")) {
              setState("verified");
            } else {
              // неизвестная ошибка — пропускаем, страница сама покажет ошибку
              setState("verified");
            }
          }
        }
      });
  }, []);

  if (state === "loading") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    );
  }

  if (state === "unverified") {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#F8F9FA]">
        <div className="max-w-md text-center space-y-4 px-6">
          <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center mx-auto">
            <svg
              className="w-7 h-7 text-amber-500"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25H4.5a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5H4.5a2.25 2.25 0 00-2.25 2.25m19.5 0-9.75 6.75L2.25 6.75"
              />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-slate-800">Verify your email to continue</h2>
          <p className="text-slate-500 text-sm">
            We sent a verification link to{" "}
            {email ? (
              <span className="font-medium text-slate-700">{email}</span>
            ) : (
              "your email"
            )}
            . Click the link in the email to activate your account.
          </p>
          <p className="text-slate-400 text-xs">Didn&apos;t get it? Check your spam folder.</p>
        </div>
      </div>
    );
  }

  return <VerificationContext.Provider value={state}>{children}</VerificationContext.Provider>;
}
