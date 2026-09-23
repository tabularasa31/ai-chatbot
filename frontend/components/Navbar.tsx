"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, clearSession } from "@/lib/api";
import { Logo } from "@/components/Logo";

export function Navbar({
  initialEmail = null,
}: {
  /** Session email decoded server-side in the layout; when present, skips the /auth/me fetch. */
  initialEmail?: string | null;
}) {
  const router = useRouter();
  const [isVerified, setIsVerified] = useState<boolean | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(initialEmail);

  useEffect(() => {
    api.clients
      .getMe()
      .catch(() => null)
      .then((client) => setIsVerified(client ? client.is_verified : null));
    if (!initialEmail) {
      api.auth
        .getMe()
        .catch(() => null)
        .then((user) => setUserEmail(user?.email ?? null));
    }
  }, [initialEmail]);

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
            <Link href="/dashboard" aria-label="Chat9 dashboard">
              <Logo size={18} textClassName="text-[15px]" />
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
