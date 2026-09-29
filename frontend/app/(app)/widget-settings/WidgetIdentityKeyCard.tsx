"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";

type Props = {
  hasKey: boolean;
  onKeyChange: () => void;
};

export default function WidgetIdentityKeyCard({ hasKey, onKeyChange }: Props) {
  const [secret, setSecret] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [confirmingRotate, setConfirmingRotate] = useState(false);

  async function reveal() {
    if (secret) {
      setRevealed(true);
      return;
    }
    setError("");
    setLoading(true);
    try {
      const data = await api.widgetIdentity.get();
      setSecret(data.secret);
      setRevealed(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("This key can't be read. Rotate it to issue a new one.");
      } else if (e instanceof ApiError) {
        setError(e.message);
      } else {
        setError("Failed to load the key");
      }
    } finally {
      setLoading(false);
    }
  }

  function hide() {
    setRevealed(false);
    setSecret(null);
  }

  async function generateOrRotate() {
    setError("");
    setLoading(true);
    try {
      const data = await api.widgetIdentity.rotate();
      setSecret(data.secret);
      setRevealed(true);
      setConfirmingRotate(false);
      onKeyChange();
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setError("Too many key rotations. Try again in an hour.");
      } else if (e instanceof ApiError) {
        setError(e.message);
      } else {
        setError("Failed to generate the key");
      }
    } finally {
      setLoading(false);
    }
  }

  async function copySecret() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Copy failed — select the key manually.");
    }
  }

  return (
    <section
      id="widget-identity-key"
      className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4 scroll-mt-24"
    >
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Identity</p>
        <h2 className="mt-1 text-base font-semibold text-slate-800">Widget identity verification</h2>
        <p className="mt-1 text-sm text-slate-500">
          If you pass a <code className="text-slate-700">userId</code> to the widget so a visitor&apos;s
          conversation follows them across devices, sign it with this key on your server so Chat9 can
          verify it belongs to that visitor.{" "}
          <a
            href="/docs/embedding-the-widget#identity-verification"
            target="_blank"
            rel="noopener noreferrer"
            className="text-violet-600 hover:underline"
          >
            How to sign user_id
          </a>
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      {!hasKey && !secret && (
        <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-100 px-3 py-2 rounded-lg">
          <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
          No key yet. User IDs from your page personalize the chat, but conversations only continue across
          devices for visitors verified by your server.
        </div>
      )}

      {hasKey && (
        <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
          <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
          Identity key configured
        </div>
      )}

      {revealed && secret && (
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-xs break-all bg-slate-50 border border-slate-200 text-slate-900 font-mono rounded px-3 py-2 flex-1 min-w-0">
            {secret}
          </code>
          <button
            type="button"
            onClick={copySecret}
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm shrink-0"
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <button
            type="button"
            onClick={hide}
            className="rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 text-sm shrink-0"
          >
            Hide
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {hasKey && !revealed && (
          <button
            type="button"
            onClick={reveal}
            disabled={loading}
            className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
          >
            {loading ? "Loading…" : "Reveal key"}
          </button>
        )}

        {!hasKey && (
          <button
            type="button"
            onClick={generateOrRotate}
            disabled={loading}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
          >
            {loading ? "Generating…" : "Generate key"}
          </button>
        )}

        {hasKey && !confirmingRotate && (
          <button
            type="button"
            onClick={() => setConfirmingRotate(true)}
            disabled={loading}
            className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
          >
            Rotate key
          </button>
        )}
      </div>

      {confirmingRotate && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 space-y-3">
          <p className="text-sm text-amber-900">
            Rotating replaces the key immediately. The old key stops working right away, and any signed
            visitors will be treated as unverified until your server signs with the new key.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={generateOrRotate}
              disabled={loading}
              className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              {loading ? "Rotating…" : "Rotate now"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingRotate(false)}
              disabled={loading}
              className="px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium rounded-lg border border-slate-200 disabled:opacity-40 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
