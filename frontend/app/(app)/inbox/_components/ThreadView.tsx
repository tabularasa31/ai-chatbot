"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { HandoffState } from "@/lib/api";
import { SkeletonRegion } from "@/components/ui/skeleton";
import { LoadError } from "@/components/ui/load-error";
import { useThread } from "@/hooks/useApi";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { ThreadHeader } from "./ThreadHeader";

const THREAD_REFRESH_MS: Record<HandoffState, number> = {
  live: 4_000,
  waiting: 8_000,
  bot: 15_000,
};
const STICK_TO_BOTTOM_PX = 40;
const SKELETON_BUBBLES = ["w-2/3", "w-1/2 ml-auto", "w-3/4"];

function ThreadSkeleton() {
  return (
    <SkeletonRegion>
      <div className="space-y-2 border-b border-line p-4">
        <div className="h-6 w-1/3 rounded-tag bg-surface-alt" />
        <div className="h-4 w-1/4 rounded-tag bg-surface-alt" />
      </div>
      <div className="space-y-3 p-4">
        {SKELETON_BUBBLES.map((width) => (
          <div key={width} className={`h-11 rounded-bubble bg-surface-alt ${width}`} />
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function ThreadView({
  sessionId,
  canOperate,
  viewerId,
  onChanged,
}: {
  sessionId: string;
  canOperate: boolean | undefined;
  viewerId: string | undefined;
  onChanged: () => Promise<unknown>;
}) {
  const [refreshMs, setRefreshMs] = useState(THREAD_REFRESH_MS.bot);
  const { data: thread, error, mutate } = useThread(sessionId, refreshMs);
  const scrollRef = useRef<HTMLDivElement>(null);
  const messageCount = thread?.messages.length ?? 0;
  const handoffState = thread?.handoff_state;

  useEffect(() => {
    if (handoffState) setRefreshMs(THREAD_REFRESH_MS[handoffState]);
  }, [handoffState]);

  // Follow new messages only when the operator is already at the bottom;
  // someone reading history must not be yanked down by a poll.
  const stickToBottom = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messageCount]);

  const changed = useCallback(async () => {
    await Promise.all([mutate(), onChanged()]);
  }, [mutate, onChanged]);

  if (!thread) {
    return error ? (
      <div className="p-4">
        <LoadError message="Couldn't load this conversation." onRetry={() => mutate()} />
      </div>
    ) : (
      <ThreadSkeleton />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <ThreadHeader thread={thread} viewerId={viewerId} />
      {error && (
        <div className="shrink-0 px-4 pt-4">
          <LoadError message="Couldn't refresh the conversation." onRetry={() => mutate()} />
        </div>
      )}
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX;
        }}
        className="min-h-0 flex-1 overflow-y-auto p-4"
      >
        {thread.messages.length === 0 ? (
          <p className="text-ui text-fg-muted">No messages yet.</p>
        ) : (
          <div className="space-y-3">
            {thread.messages.map((msg) => (
              <MessageBubble key={msg.id} msg={msg} viewerId={viewerId} />
            ))}
          </div>
        )}
      </div>
      {canOperate === true && <Composer thread={thread} onChanged={changed} />}
      {canOperate === false && (
        <p className="shrink-0 border-t border-line p-4 text-ui text-fg-muted">
          You can read conversations. Answering, taking and resolving need an operator seat.
        </p>
      )}
    </div>
  );
}
