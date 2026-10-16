"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { api, clearSession } from "@/lib/api";
import { LogoMark } from "@/components/Logo";
import { useAuthUser } from "@/hooks/useApi";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface";

export function Navbar({
  initialEmail = null,
}: {
  /** Session email decoded server-side in the layout; when present, skips the /auth/me fetch. */
  initialEmail?: string | null;
}) {
  const router = useRouter();
  const { data: authUser } = useAuthUser(!initialEmail);
  const userEmail = initialEmail ?? authUser?.email ?? null;

  function handleLogout() {
    clearSession();
    api.auth.logout();
    router.replace("/login");
  }

  return (
    <div>
      <header className="fixed left-0 right-0 top-0 z-[100] flex h-12 items-center justify-between border-b border-line bg-surface px-6">
        <Link
          href="/dashboard"
          aria-label="Chat9 home"
          className={`flex h-11 items-center gap-2 rounded-control ${FOCUS_RING}`}
        >
          <LogoMark size={18} tile={false} />
          <span className="text-body font-semibold leading-none text-fg">Chat9</span>
        </Link>
        <div className="flex items-center gap-4">
          {userEmail && (
            <span title={userEmail} className="max-w-72 truncate text-ui text-fg-muted">
              {userEmail}
            </span>
          )}
          <button
            type="button"
            onClick={handleLogout}
            className={`inline-flex h-11 items-center gap-2 rounded-control border border-line-strong bg-surface px-4 text-ui font-medium text-fg transition-colors hover:bg-surface-alt active:bg-surface-alt ${FOCUS_RING}`}
          >
            <LogOut size={16} aria-hidden="true" />
            Log out
          </button>
        </div>
      </header>
    </div>
  );
}
