import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  draftErrorCode,
  HTTP_TOO_MANY_REQUESTS,
  staleDraft,
  type UnansweredDraft,
  type UnansweredPublishResult,
} from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export type DraftPhase =
  | { status: "loading" }
  | { status: "generating" }
  | { status: "loadError" }
  | { status: "ready" };

export type DraftNotice =
  | { kind: "no_key" }
  | { kind: "generation_failed" }
  | { kind: "rate_limited" }
  | { kind: "refine_rejected" }
  | { kind: "refine_failed" }
  | { kind: "save_failed" }
  | { kind: "group_answered" }
  | { kind: "conflict"; current: UnansweredDraft | null }
  | { kind: "reloaded" };

const isRateLimited = (err: unknown) => err instanceof ApiError && err.status === HTTP_TOO_MANY_REQUESTS;

const wait = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/** Resolves to the group's draft, or to null when there is none and generation has to be started. */
async function fetchDraft(groupId: string): Promise<UnansweredDraft | null> {
  try {
    return await api.unanswered.getDraft(groupId);
  } catch (err) {
    if (draftErrorCode(err) === "no_draft") return null;
    throw err;
  }
}

async function generateDraft(groupId: string): Promise<UnansweredDraft> {
  try {
    return await api.unanswered.generateDraft(groupId);
  } catch (err) {
    if (draftErrorCode(err) !== "draft_generating") throw err;
    return api.unanswered.getDraft(groupId);
  }
}

function loadFailureNotice(err: unknown): DraftNotice | null {
  if (isRateLimited(err)) return { kind: "rate_limited" };
  switch (draftErrorCode(err)) {
    case "no_openai_key":
      return { kind: "no_key" };
    case "draft_generation_failed":
    case "draft_rejected":
      return { kind: "generation_failed" };
    default:
      return null;
  }
}

export function useAnswerDraft(groupId: string, groupQuestion: string, onStored: () => void) {
  const [phase, setPhase] = useState<DraftPhase>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [server, setServer] = useState<UnansweredDraft | null>(null);
  const [draftApplied, setDraftApplied] = useState(false);
  const [examples, setExamples] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [prefilledQuestion, setPrefilledQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [instructions, setInstructions] = useState("");
  const [notice, setNotice] = useState<DraftNotice | null>(null);
  const [keptText, setKeptText] = useState<string | null>(null);
  const [pending, setPending] = useState<"save" | "refine" | null>(null);
  const keepTextOnReload = useRef(false);

  const dirty = question !== (server?.question ?? prefilledQuestion) || answer !== (server?.answer ?? "");

  const onStoredRef = useRef(onStored);
  onStoredRef.current = onStored;

  const applyDraft = useCallback((draft: UnansweredDraft) => {
    setServer(draft);
    setDraftApplied(true);
    setExamples(draft.examples);
    setQuestion(draft.question);
    setAnswer(draft.answer);
    setPhase({ status: "ready" });
    onStoredRef.current();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const reloadedWithKeptText = keepTextOnReload.current;
    keepTextOnReload.current = false;
    void (async () => {
      let polling = false;
      try {
        let draft = await fetchDraft(groupId);
        if (!draft) {
          if (cancelled) return;
          setPhase({ status: "generating" });
          polling = true;
          draft = await generateDraft(groupId);
        }
        while (draft.generating) {
          if (cancelled) return;
          setExamples(draft.examples);
          setPhase({ status: "generating" });
          polling = true;
          await wait(POLL_INTERVAL_MS);
          if (cancelled) return;
          draft = await api.unanswered.getDraft(groupId);
        }
        if (cancelled) return;
        applyDraft(draft);
        if (reloadedWithKeptText) setNotice({ kind: "reloaded" });
      } catch (err) {
        if (cancelled) return;
        const failure: DraftNotice | null =
          polling && draftErrorCode(err) === "no_draft" ? { kind: "generation_failed" } : loadFailureNotice(err);
        setNotice(failure);
        if (failure && !reloadedWithKeptText) {
          setQuestion(groupQuestion);
          setPrefilledQuestion(groupQuestion);
        }
        setPhase({ status: failure ? "ready" : "loadError" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId, groupQuestion, attempt, applyDraft]);

  function retryLoad() {
    setNotice(null);
    setDraftApplied(false);
    setPhase({ status: "loading" });
    setAttempt((count) => count + 1);
  }

  function reloadKeepingText(current: UnansweredDraft | null) {
    setKeptText(`${question}\n\n${answer}`);
    if (current) {
      applyDraft(current);
      setNotice({ kind: "reloaded" });
      return;
    }
    keepTextOnReload.current = true;
    retryLoad();
  }

  function failWrite(err: unknown, fallback: DraftNotice) {
    const code = draftErrorCode(err);
    if (code === "stale_version") setNotice({ kind: "conflict", current: staleDraft(err) });
    else if (code === "draft_generating") reloadKeepingText(null);
    else if (code === "group_answered") setNotice({ kind: "group_answered" });
    else if (isRateLimited(err)) setNotice({ kind: "rate_limited" });
    else setNotice(fallback);
  }

  async function persist(): Promise<UnansweredDraft> {
    const saved = await api.unanswered.saveDraft(groupId, { question, answer, version: server?.version ?? 0 });
    applyDraft(saved);
    return saved;
  }

  async function saveDraft(): Promise<boolean> {
    if (pending) return false;
    setPending("save");
    setNotice(null);
    try {
      await persist();
      return true;
    } catch (err) {
      failWrite(err, { kind: "save_failed" });
      return false;
    } finally {
      setPending(null);
    }
  }

  async function askAgent() {
    if (!server || pending || !instructions.trim()) return;
    setPending("refine");
    setNotice(null);
    try {
      applyDraft(await api.unanswered.refineDraft(groupId, { instructions, version: server.version }));
      setInstructions("");
    } catch (err) {
      failWrite(err, { kind: draftErrorCode(err) === "draft_rejected" ? "refine_rejected" : "refine_failed" });
    } finally {
      setPending(null);
    }
  }

  /** Rejects when publishing failed; resolves to null when the draft turned out to be stale. */
  async function publish(): Promise<UnansweredPublishResult | null> {
    try {
      const stored = dirty || !server ? await persist() : server;
      return await api.unanswered.publish(groupId, stored.version);
    } catch (err) {
      if (draftErrorCode(err) !== "stale_version") throw err;
      failWrite(err, { kind: "save_failed" });
      return null;
    }
  }

  function reloadDraft() {
    if (notice?.kind === "conflict") reloadKeepingText(notice.current);
  }

  return {
    phase,
    notice,
    keptText,
    examples,
    pending,
    dirty,
    question,
    answer,
    instructions,
    canAskAgent: server !== null,
    draftApplied,
    setQuestion,
    setAnswer,
    setInstructions,
    saveDraft,
    askAgent,
    publish,
    reloadDraft,
    retryLoad,
  };
}
