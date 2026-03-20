"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, removeToken } from "@/lib/api";

const navLinkClass =
  "text-[#FAF5FF]/70 hover:text-[#FAF5FF] text-sm";

export function Navbar() {
  const router = useRouter();
  const [isAdmin, setIsAdmin] = useState(false);
  const [isVerified, setIsVerified] = useState<boolean | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.clients.getMe().catch(() => null),
      api.auth.getMe().catch(() => null),
    ]).then(([client, user]) => {
      if (client) {
        setIsAdmin(client.is_admin);
        setIsVerified(client.is_verified);
      } else {
        setIsAdmin(false);
        setIsVerified(null);
      }
      // /clients/me has no email in API; auth/me provides it for the navbar
      setUserEmail(user?.email ?? null);
    });
  }, []);

  function handleLogout() {
    removeToken();
    router.replace("/login");
  }

  return (
    <nav>
      {isVerified === false && (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-800 px-4 py-2 text-center text-sm">
          Your email is not verified. Check your inbox for a verification link.
        </div>
      )}
      <div style={{ backgroundColor: "#0A0A0F" }} className="w-full">
        <div className="max-w-4xl mx-auto px-4">
          <div className="flex items-center justify-between h-12">
            <div className="flex items-center gap-6">
              <Link href="/dashboard" className="text-[#FAF5FF] font-semibold">
                Chat9
              </Link>
              <Link href="/dashboard" className={navLinkClass}>
                Dashboard
              </Link>
              {isAdmin && (
                <Link href="/admin/metrics" className={navLinkClass}>
                  Admin
                </Link>
              )}
              <Link href="/documents" className={navLinkClass}>
                Documents
              </Link>
              <Link href="/logs" className={navLinkClass}>
                Logs
              </Link>
              <Link href="/review" className={navLinkClass}>
                Review
              </Link>
              <Link href="/debug" className={navLinkClass}>
                Debug
              </Link>
            </div>
            <div className="flex items-center gap-4">
              {userEmail && (
                <span className="text-[#FAF5FF]/70 text-sm">{userEmail}</span>
              )}
              <button
                type="button"
                onClick={handleLogout}
                className="text-[#E879F9] text-sm font-medium hover:text-[#E879F9]/80"
              >
                Logout
              </button>
            </div>
          </div>
        </div>
      </div>
    </nav>
  );
}
