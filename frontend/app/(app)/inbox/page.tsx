"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { mutate as mutateGlobal } from "swr";
import { CountBadge } from "@/components/ui/count-badge";
import { SectionTabs, sectionTabId, type SectionTab } from "@/components/ui/section-tabs";
import { cn } from "@/components/ui/utils";
import { useAuthUser, useClientMe, useInbox, useInboxSummary } from "@/hooks/useApi";
import { InboxList, type Scope } from "./_components/InboxList";
import { ThreadView } from "./_components/ThreadView";

const INBOX_REFRESH_MS = 15_000;
const SEARCH_DEBOUNCE_MS = 300;
const NOW_TICK_MS = 30_000;
const PANEL_ID = "inbox-list";
const PANES_MIN_HEIGHT = "min-h-[420px]";

function inboxHref(scope: Scope, session: string | null): string {
  const params = new URLSearchParams();
  if (scope === "all") params.set("scope", "all");
  if (session) params.set("session", session);
  const query = params.toString();
  return query ? `/inbox?${query}` : "/inbox";
}

function InboxPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selected = searchParams.get("session");
  const scope: Scope = searchParams.get("scope") === "all" ? "all" : "attention";
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [search]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), NOW_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const { data: me } = useClientMe();
  const { data: authUser } = useAuthUser();
  const { data: inbox, error, isCurrent, mutate } = useInbox(scope, query, INBOX_REFRESH_MS);
  const { data: inboxSummary } = useInboxSummary();
  const canOperate = me ? Boolean(me.has_seat) : undefined;

  const changeScope = useCallback(
    (next: Scope) => router.push(inboxHref(next, selected)),
    [router, selected],
  );
  const select = useCallback(
    (sessionId: string) => router.replace(inboxHref(scope, sessionId)),
    [router, scope],
  );

  const rows = useMemo(() => inbox?.items ?? [], [inbox]);
  const refresh = useCallback(async () => {
    await mutate();
    mutateGlobal("operator/inbox/summary");
  }, [mutate]);

  const tabs: readonly SectionTab<Scope>[] = [
    {
      id: "attention",
      label: "Needs attention",
      suffix: <CountBadge count={inboxSummary?.attention_count ?? 0} tone="brand" meaning="need attention" />,
    },
    { id: "all", label: "All" },
  ];

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-h1 text-fg">Inbox</h1>
        <SectionTabs label="Conversation scope" tabs={tabs} activeId={scope} onChange={changeScope} panelId={PANEL_ID} />
      </div>

      <div className={cn("relative flex-1", PANES_MIN_HEIGHT)}>
        <div className="absolute inset-0 flex overflow-hidden rounded-surface border border-line bg-surface">
          <section
            id={PANEL_ID}
            role="tabpanel"
            aria-labelledby={sectionTabId(PANEL_ID, scope)}
            className="flex w-1/3 min-w-72 max-w-sm shrink-0 flex-col border-r border-line"
          >
            <InboxList
              rows={rows}
              error={error}
              isCurrent={isCurrent}
              scope={scope}
              search={search}
              query={query}
              viewerId={authUser?.id}
              selected={selected}
              now={now}
              onSearchChange={setSearch}
              onSelect={select}
              onShowAll={() => changeScope("all")}
              onRetry={refresh}
            />
          </section>
          <section aria-label="Conversation" className="min-w-0 flex-1">
            {selected ? (
              <ThreadView
                key={selected}
                sessionId={selected}
                canOperate={canOperate}
                viewerId={authUser?.id}
                onChanged={refresh}
              />
            ) : (
              rows.length > 0 && <p className="p-6 text-ui text-fg-muted">Select a conversation.</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={<h1 className="text-h1 text-fg">Inbox</h1>}>
      <InboxPageContent />
    </Suspense>
  );
}
