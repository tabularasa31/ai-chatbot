"use client";

import { NotFoundContent } from "@/components/NotFoundContent";
import { cn } from "@/components/ui/utils";
import { MarketingShell } from "./MarketingShell";
import { CONTAINER } from "./classes";
import { useSignedIn } from "./useSignedIn";

const HOME_ACTION = { href: "/", label: "Go to home page" };
const INBOX_ACTION = { href: "/inbox", label: "Go to Inbox" };

export function SiteNotFound() {
  const signedIn = useSignedIn();

  return (
    <MarketingShell>
      <div className={cn(CONTAINER, "py-16")}>
        <NotFoundContent action={signedIn ? INBOX_ACTION : HOME_ACTION} />
      </div>
    </MarketingShell>
  );
}
