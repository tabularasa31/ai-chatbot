"use client";

import { ReactNode } from "react";

/** Neon Dusk dark theme — shared layout and form styles for auth pages */
export function AuthCard({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-[#0A0A0F] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="bg-[#1E1E2E] border border-[#2E2E3E] rounded-lg shadow-md p-8">
          {children}
        </div>
      </div>
    </div>
  );
}

/** Centered card for success/error states (e.g. forgot-password success, verify) */
export function AuthCardCentered({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-[#0A0A0F] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="bg-[#1E1E2E] border border-[#2E2E3E] rounded-lg shadow-md p-8 text-center">
          {children}
        </div>
      </div>
    </div>
  );
}

export const authStyles = {
  heading: "text-2xl font-semibold text-[#FAF5FF] mb-6",
  headingSm: "text-2xl font-semibold text-[#FAF5FF] mb-2",
  subtext: "text-[#FAF5FF]/80 text-sm mb-6",
  label: "block text-sm font-medium text-[#FAF5FF]/80 mb-1",
  input:
    "w-full px-3 py-2 bg-[#0A0A0F] border border-[#2E2E3E] rounded-md text-[#FAF5FF] placeholder-[#FAF5FF]/40 focus:outline-none focus:ring-2 focus:ring-[#E879F9] focus:ring-offset-2 focus:ring-offset-[#0A0A0F] focus:border-transparent",
  button:
    "w-full py-2 px-4 bg-[#E879F9] text-[#0A0A0F] font-medium rounded-md hover:bg-[#f099fb] hover:scale-105 transition-all focus:outline-none focus:ring-2 focus:ring-[#E879F9] focus:ring-offset-2 focus:ring-offset-[#0A0A0F] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100",
  error: "text-[#F87171] text-sm bg-[#F87171]/10 border border-[#F87171]/30 px-3 py-2 rounded-md",
  success: "text-[#4ADE80] text-sm bg-[#4ADE80]/10 border border-[#4ADE80]/30 px-3 py-2 rounded-md",
  link: "text-[#E879F9] hover:underline",
  footer: "mt-4 text-center text-[#FAF5FF]/60 text-sm",
} as const;
