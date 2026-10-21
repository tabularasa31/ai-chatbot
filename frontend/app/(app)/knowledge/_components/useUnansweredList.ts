import { useCallback, useEffect, useRef, useState } from "react";
import { api, type UnansweredGroup, type UnansweredStatus } from "@/lib/api";

export type ListState =
  | { status: "loading" }
  | { status: "error" }
  | {
      status: "ready";
      items: UnansweredGroup[];
      nextOffset: number | null;
      tenantConversations30d: number;
      tenantHasConversations: boolean;
      refreshFailed: boolean;
    };

export function useUnansweredList(status: UnansweredStatus) {
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [loadingMore, setLoadingMore] = useState(false);
  // Mirrors `state` synchronously so overlapping async writers always start from the latest list.
  const latest = useRef(state);
  const latestRequest = useRef(0);
  const removedIds = useRef<string[]>([]);

  const update = useCallback((change: (prev: ListState) => ListState) => {
    latest.current = change(latest.current);
    setState(latest.current);
  }, []);

  const load = useCallback(async () => {
    const request = ++latestRequest.current;
    removedIds.current = [];
    setLoadingMore(false);
    try {
      const data = await api.unanswered.list(status);
      if (request !== latestRequest.current) return;
      update(() => ({
        status: "ready",
        items: data.items,
        nextOffset: data.next_offset,
        tenantConversations30d: data.tenant_conversations_30d,
        tenantHasConversations: data.tenant_has_conversations,
        refreshFailed: false,
      }));
    } catch {
      if (request !== latestRequest.current) return;
      update((prev) => (prev.status === "ready" ? { ...prev, refreshFailed: true } : { status: "error" }));
    }
  }, [status, update]);

  useEffect(() => {
    update(() => ({ status: "loading" }));
    void load();
  }, [load, update]);

  const loadMore = useCallback(async () => {
    const snapshot = latest.current;
    if (snapshot.status !== "ready" || snapshot.nextOffset === null) return;
    const request = latestRequest.current;
    const removedBefore = removedIds.current.length;
    setLoadingMore(true);
    try {
      const data = await api.unanswered.list(status, snapshot.nextOffset);
      if (request !== latestRequest.current) return;
      const removedMeanwhile = removedIds.current.length - removedBefore;
      update((prev) => {
        if (prev.status !== "ready") return prev;
        const skip = new Set([...prev.items.map((item) => item.id), ...removedIds.current]);
        return {
          ...prev,
          items: [...prev.items, ...data.items.filter((item) => !skip.has(item.id))],
          nextOffset: data.next_offset === null ? null : data.next_offset - removedMeanwhile,
          refreshFailed: false,
        };
      });
    } catch {
      if (request !== latestRequest.current) return;
      update((prev) => (prev.status === "ready" ? { ...prev, refreshFailed: true } : prev));
    } finally {
      if (request === latestRequest.current) setLoadingMore(false);
    }
  }, [status, update]);

  /** Returns the removed row's index, or -1 when the row is not in the current list. */
  const remove = useCallback(
    (id: string): number => {
      const prev = latest.current;
      if (prev.status !== "ready") return -1;
      const index = prev.items.findIndex((item) => item.id === id);
      if (index === -1) return -1;
      removedIds.current.push(id);
      update(() => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== id),
        nextOffset: prev.nextOffset === null ? null : prev.nextOffset - 1,
      }));
      return index;
    },
    [update],
  );

  const markDraftStored = useCallback(
    (id: string) =>
      update((prev) =>
        prev.status === "ready"
          ? { ...prev, items: prev.items.map((item) => (item.id === id && !item.has_draft ? { ...item, has_draft: true } : item)) }
          : prev,
      ),
    [update],
  );

  return { state, loadingMore, load, loadMore, remove, markDraftStored };
}
