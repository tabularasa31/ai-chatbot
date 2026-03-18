"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { removeToken } from "@/lib/api";

export function Navbar() {
  const router = useRouter();

  function handleLogout() {
    removeToken();
    router.replace("/login");
  }

  return (
    <nav className="bg-white border-b border-slate-200 shadow-sm">
      <div className="max-w-4xl mx-auto px-4">
        <div className="flex items-center justify-between h-14">
          <div className="flex items-center gap-6">
            <Link href="/dashboard" className="text-slate-800 font-semibold hover:text-blue-600">
              AI Chatbot
            </Link>
            <Link href="/dashboard" className="text-slate-600 hover:text-slate-800 text-sm">
              Dashboard
            </Link>
            <Link href="/documents" className="text-slate-600 hover:text-slate-800 text-sm">
              Documents
            </Link>
            <Link href="/logs" className="text-slate-600 hover:text-slate-800 text-sm">
              Logs
            </Link>
            <Link href="/debug" className="text-slate-600 hover:text-slate-800 text-sm">
              Debug
            </Link>
          </div>
          <button
            onClick={handleLogout}
            className="text-slate-600 hover:text-red-600 text-sm font-medium"
          >
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}
