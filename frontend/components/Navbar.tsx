"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, clearSession } from "@/lib/api";

export function Navbar() {
  const router = useRouter();
  const [isVerified, setIsVerified] = useState<boolean | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.tenants.getMe().catch(() => null),
      api.auth.getMe().catch(() => null),
    ]).then(([client, user]) => {
      if (client) {
        setIsVerified(client.is_verified);
      } else {
        setIsVerified(null);
      }
      setUserEmail(user?.email ?? null);
    });
  }, []);

  function handleLogout() {
    clearSession();
    api.auth.logout();
    router.replace("/login");
  }

  return (
    <nav>
      {isVerified === false && (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-800 px-4 py-2 text-center text-sm">
          Your email is not verified. Check your inbox for a verification link.
        </div>
      )}
      <div className="fixed top-0 left-0 right-0 z-[100] bg-nd-base border-b border-white/[0.07]">
        <div className="px-5">
          <div className="flex items-center justify-between h-12">
            <Link href="/dashboard" className="text-nd-text font-bold text-[15px] tracking-tight">
              Chat9
            </Link>
            <div className="flex items-center gap-5">
              {userEmail && (
                <span className="text-nd-text/50 text-xs">{userEmail}</span>
              )}
              <button
                type="button"
                onClick={handleLogout}
                className="text-nd-accent text-xs font-medium hover:text-nd-accent/80"
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
