"use client";

import { useEffect, useState } from "react";
import { api, type ChatDebugResponse } from "@/lib/api";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";

function DebugStateMessage({
  tone,
  title,
  description,
}: {
  tone: "danger" | "neutral";
  title: string;
  description: string;
}) {
  return (
    <div
      className={`rounded-xl border p-6 ${
        tone === "danger"
          ? "border-rose-200 bg-rose-50 text-rose-900"
          : "border-slate-200 bg-white text-slate-800"
      }`}
    >
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="mt-2 text-sm leading-6 opacity-80">{description}</p>
    </div>
  );
}

export default function DebugPage() {
  const [botId, setBotId] = useState("");
  const [botLoading, setBotLoading] = useState(true);
  const [botError, setBotError] = useState("");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ChatDebugResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadClient() {
      setBotLoading(true);
      setBotError("");
      try {
        const client = await api.clients.getMe();
        if (cancelled) return;
        setBotId(client.public_id);
      } catch (err) {
        if (cancelled) return;
        setBotError(err instanceof Error ? err.message : "Failed to load bot context");
      } finally {
        if (!cancelled) {
          setBotLoading(false);
        }
      }
    }

    loadClient();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRun() {
    const q = question.trim();
    if (!q) return;
    setError("");
    setResult(null);
    setLoading(true);
    try {
      const data = await api.chat.debug(q, botId);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Debug failed");
    } finally {
      setLoading(false);
    }
  }

  function truncateId(id: string) {
    if (id.length <= 12) return id;
    return `${id.slice(0, 8)}...${id.slice(-4)}`;
  }

  if (botLoading) {
    return (
      <DebugStateMessage
        tone="neutral"
        title="Preparing debug console"
        description="Loading the current bot context so RAG debug can run without extra parameters."
      />
    );
  }

  if (!botId) {
    return (
      <DebugStateMessage
        tone="danger"
        title="Debug temporarily unavailable"
        description={botError || "Couldn't determine the current bot for debug."}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-amber-600 text-sm font-medium mb-1">
          Debug console (internal use)
        </p>
        <h1 className="text-2xl font-semibold text-slate-800">RAG Debug</h1>
        <p className="text-slate-500 text-sm mt-1">
          Run a question through the RAG pipeline and inspect retrieval results.
        </p>
        {botId && (
          <p className="text-slate-500 text-sm mt-1">
            Bot: <span className="font-mono text-slate-700">{botId}</span>
          </p>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <label className="block text-sm font-medium text-slate-700 mb-2">
          Question
        </label>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Type your question..."
          rows={3}
          className="w-full px-3 py-2 border border-slate-200 rounded-lg text-slate-800 placeholder:text-slate-400 outline-none focus:border-slate-400"
        />
        <button
          onClick={handleRun}
          disabled={loading || !question.trim()}
          className="mt-3 px-4 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? "Running..." : "Run debug"}
        </button>
        {error && (
          <p className="mt-3 text-red-600 text-sm">{error}</p>
        )}
      </div>

      {result && (
        <>
          <div className="bg-white rounded-xl border border-slate-200 p-6">
            <h2 className="text-base font-semibold text-slate-800 mb-2">Answer</h2>
            <CodeBlockWithCopy
              code={result.answer}
              copyLabel="Copy answer"
              preClassName="bg-slate-100 rounded-md text-sm text-slate-800 whitespace-pre-wrap break-words"
            />
            <p className="text-slate-500 text-xs mt-2">
              Tokens used: {result.tokens_used}
            </p>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-6">
            <h2 className="text-base font-semibold text-slate-800 mb-2">
              Debug info
            </h2>
            <div className="mb-3 space-y-1 text-slate-600 text-sm">
              <p>
                Strategy:{" "}
                <span className="font-mono font-medium text-slate-800">
                  {result.debug.strategy ?? "n/a"}
                </span>
              </p>
              <p>
                Reject reason:{" "}
                <span className="font-mono font-medium text-slate-800">
                  {result.debug.reject_reason ?? "n/a"}
                </span>
              </p>
              <p>
                Validation outcome:{" "}
                <span className="font-mono font-medium text-slate-800">
                  {result.debug.validation_outcome ?? "n/a"}
                </span>
              </p>
              <p>
                Top match score:{" "}
                <span className="font-mono font-medium text-slate-800">
                  {result.debug.best_rank_score?.toFixed(4) ?? "n/a"}
                </span>
              </p>
              <p>
                Confidence score:{" "}
                <span className="font-mono font-medium text-slate-800">
                  {result.debug.best_confidence_score?.toFixed(4) ?? "n/a"}
                </span>
              </p>
            </div>
            {result.debug.chunks.length === 0 ? (
              <p className="text-slate-500 text-sm">No chunks retrieved.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200">
                      <th className="text-left py-2 pr-4 text-slate-600 font-medium">
                        document_id
                      </th>
                      <th className="text-left py-2 pr-4 text-slate-600 font-medium">
                        score
                      </th>
                      <th className="text-left py-2 text-slate-600 font-medium">
                        preview
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.debug.chunks.map((chunk, i) => (
                      <tr key={i} className="border-b border-slate-100">
                        <td className="py-2 pr-4 font-mono text-xs text-slate-700">
                          {truncateId(chunk.document_id)}
                        </td>
                        <td className="py-2 pr-4 text-slate-700">
                          {chunk.score.toFixed(4)}
                        </td>
                        <td className="py-2">
                          <div className="max-h-24 overflow-y-auto text-slate-700 max-w-md">
                            {chunk.preview}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
