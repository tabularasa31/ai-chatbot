"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { api, clearSession } from "@/lib/api";
import { LogoMark } from "@/components/Logo";
import { Button, FOCUS_RING } from "@/components/ui/button";
import { ThemeMenu } from "@/components/ui/theme-menu";
import { useAuthUser } from "@/hooks/useApi";

const LOGOUT_CLASSES =
  "border-chrome-line-strong bg-transparent text-chrome-fg hover:bg-chrome-hover active:bg-chrome-hover";

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
    <header className="fixed left-0 right-0 top-0 z-50 flex h-12 items-center justify-between border-b border-chrome-line bg-chrome px-6">
      <Link
        href="/dashboard"
        aria-label="Chat9 home"
        className={`flex h-11 items-center gap-2 rounded-control ${FOCUS_RING.chrome}`}
      >
        <LogoMark size={18} tile={false} />
        <span className="text-body font-semibold leading-none text-chrome-fg">Chat9</span>
      </Link>
      <div className="flex min-w-0 items-center gap-4">
        {userEmail && (
          <span title={userEmail} className="max-w-72 truncate text-ui text-chrome-fg-muted">
            {userEmail}
          </span>
        )}
        <ThemeMenu surface="chrome" side="below" align="end" />
        <Button variant="secondary" offset="chrome" className={LOGOUT_CLASSES} onClick={handleLogout}>
          <LogOut size={16} aria-hidden="true" />
          Log out
        </Button>
      </div>
    </header>
  );
}
