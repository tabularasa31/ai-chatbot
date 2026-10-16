"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { api, clearSession } from "@/lib/api";
import { LogoMark } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { useAuthUser } from "@/hooks/useApi";

const CHROME_FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-chrome-accent focus-visible:ring-offset-2 focus-visible:ring-offset-chrome";
const LOGOUT_CLASSES = cn(
  "border-chrome-line-strong bg-transparent text-chrome-fg hover:bg-chrome-hover active:bg-chrome-hover",
  CHROME_FOCUS_RING,
);

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
        className={`flex h-11 items-center gap-2 rounded-control ${CHROME_FOCUS_RING}`}
      >
        <LogoMark size={18} tile={false} />
        <span className="text-body font-semibold leading-none text-chrome-fg">Chat9</span>
      </Link>
      <div className="flex items-center gap-4">
        {userEmail && (
          <span title={userEmail} className="max-w-72 truncate text-ui text-chrome-fg-muted">
            {userEmail}
          </span>
        )}
        <Button variant="secondary" offset="surface" className={LOGOUT_CLASSES} onClick={handleLogout}>
          <LogOut size={16} aria-hidden="true" />
          Log out
        </Button>
      </div>
    </header>
  );
}
