"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  type RotateTenantApiKeyResponse,
  type TenantApiKeyResponse,
} from "@/lib/api";

type Reason = "leaked" | "scheduled" | "compromise" | "other";

const REASON_OPTIONS: { value: Reason; label: string; description: string }[] = [
  {
    value: "scheduled",
    label: "Scheduled rotation",
    description: "Routine rotation. Recommended on a regular cadence.",
  },
  {
    value: "leaked",
    label: "Possibly leaked",
    description: "Key may have been exposed (committed to a repo, sent in email, etc).",
  },
  {
    value: "compromise",
    label: "Compromised",
    description: "Confirmed compromise. Use this together with immediate revoke.",
  },
  {
    value: "other",
    label: "Other",
    description: "Reason not listed above.",
  },
];

function StatusBadge({ status }: { status: TenantApiKeyResponse["status"] }) {
  const styles: Record<TenantApiKeyResponse["status"], string> = {
    active: "bg-emerald-100 text-emerald-700",
    revoking: "bg-amber-100 text-amber-700",
    revoked: "bg-slate-200 text-slate-600",
  };
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded ${styles[status]}`}>
      {status}
    </span>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function relativeRemaining(expires_at: string | null): string | null {
  if (!expires_at) return null;
  const ms = new Date(expires_at).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const hours = Math.floor(ms / 3600_000);
  const minutes = Math.floor((ms % 3600_000) / 60_000);
  if (hours > 0) return `${hours}h ${minutes}m left`;
  return `${minutes}m left`;
}

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<TenantApiKeyResponse[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showRotateModal, setShowRotateModal] = useState(false);
  const [reason, setReason] = useState<Reason>("scheduled");
  const [revokeImmediately, setRevokeImmediately] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [rotationResult, setRotationResult] =
    useState<RotateTenantApiKeyResponse | null>(null);
  const [rotateError, setRotateError] = useState<string | null>(null);
  const [copiedNew, setCopiedNew] = useState(false);
  // Force re-render once a minute so the "X minutes left" labels on
  // revoking keys actually count down without a manual reload.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await api.apiKeys.list();
      setKeys(res.items);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load API keys");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onRotate() {
    setRotating(true);
    setRotateError(null);
    try {
      const result = await api.apiKeys.rotate({
        reason,
        revoke_old_immediately: revokeImmediately,
      });
      setRotationResult(result);
      setShowRotateModal(false);
      await load();
    } catch (e) {
      setRotateError(e instanceof Error ? e.message : "Failed to rotate key");
    } finally {
      setRotating(false);
    }
  }

  async function onRevoke(keyId: string) {
    if (!confirm("Revoke this key now? The widget will return 401 immediately.")) return;
    try {
      await api.apiKeys.revoke(keyId);
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to revoke key");
    }
  }

  function copyNewKey() {
    if (!rotationResult) return;
    navigator.clipboard.writeText(rotationResult.api_key);
    setCopiedNew(true);
    setTimeout(() => setCopiedNew(false), 2000);
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">API keys</h1>
        <p className="text-slate-500 text-sm mt-1">
          Rotate your widget API key without breaking the embedded widget. The
          previous key keeps working for a 24-hour grace window unless you choose
          to revoke it immediately.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-800">
              Active and recent keys
            </h2>
            <p className="text-slate-500 text-sm">
              The plaintext key is shown only once at rotation. Use the last 4
              characters to identify a key.
            </p>
          </div>
          <button
            onClick={() => {
              setReason("scheduled");
              setRevokeImmediately(false);
              setRotateError(null);
              setShowRotateModal(true);
            }}
            className="shrink-0 px-4 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-700"
          >
            Rotate key
          </button>
        </div>

        {loadError && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded-lg text-sm mb-3">
            {loadError}
          </div>
        )}

        {!keys ? (
          <div className="text-slate-400 text-sm">Loading…</div>
        ) : keys.length === 0 ? (
          <div className="text-slate-400 text-sm">No keys yet.</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {keys.map((k) => {
              const remaining = relativeRemaining(k.expires_at);
              return (
                <div key={k.id} className="py-3 flex items-center gap-3 flex-wrap">
                  <code className="px-2 py-1 bg-slate-100 rounded text-sm font-mono">
                    ck_••••{k.key_hint}
                  </code>
                  <StatusBadge status={k.status} />
                  {k.status === "revoking" && remaining && (
                    <span className="text-xs text-amber-700">{remaining}</span>
                  )}
                  <span className="text-xs text-slate-500">
                    Created {formatDate(k.created_at)}
                  </span>
                  <span className="text-xs text-slate-500">
                    Last used {formatDate(k.last_used_at)}
                  </span>
                  {k.revoked_reason && (
                    <span className="text-xs text-slate-500">
                      Reason: {k.revoked_reason}
                    </span>
                  )}
                  {k.status !== "revoked" && (
                    <button
                      onClick={() => onRevoke(k.id)}
                      className="ml-auto text-xs px-2 py-1 text-red-600 border border-red-200 rounded hover:bg-red-50"
                    >
                      Revoke now
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {rotationResult && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-6">
          <h2 className="text-base font-semibold text-emerald-900 mb-1">
            New API key generated
          </h2>
          <p className="text-emerald-800 text-sm mb-3">
            Copy this key now — you won&apos;t see it again. Update your embed
            snippet with this value.
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            <code className="flex-1 min-w-0 px-3 py-2 bg-white border border-emerald-200 rounded-lg text-sm font-mono break-all">
              {rotationResult.api_key}
            </code>
            <button
              onClick={copyNewKey}
              className="px-4 py-2 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700"
            >
              {copiedNew ? "Copied!" : "Copy"}
            </button>
          </div>
          <button
            onClick={() => setRotationResult(null)}
            className="mt-3 text-xs text-emerald-700 underline"
          >
            I&apos;ve saved it — dismiss
          </button>
        </div>
      )}

      {showRotateModal && (
        <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <h2 className="text-lg font-semibold text-slate-800 mb-2">
              Rotate API key
            </h2>
            <p className="text-slate-500 text-sm mb-4">
              A new key will be generated and shown once. The previous key will
              enter a 24-hour grace window unless you choose to revoke it now.
            </p>

            <fieldset className="space-y-2 mb-4">
              <legend className="sr-only">Rotation reason</legend>
              {REASON_OPTIONS.map((opt) => (
                <div key={opt.value} className="flex items-start gap-2">
                  <input
                    id={`reason-${opt.value}`}
                    type="radio"
                    name="reason"
                    value={opt.value}
                    checked={reason === opt.value}
                    onChange={() => setReason(opt.value)}
                    className="mt-1"
                  />
                  <label
                    htmlFor={`reason-${opt.value}`}
                    className="cursor-pointer"
                  >
                    <span className="block text-sm font-medium text-slate-800">
                      {opt.label}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {opt.description}
                    </span>
                  </label>
                </div>
              ))}
            </fieldset>

            <label className="flex items-center gap-2 mb-4 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={revokeImmediately}
                onChange={(e) => setRevokeImmediately(e.target.checked)}
              />
              Revoke old key immediately (no grace period)
            </label>

            {rotateError && (
              <div className="bg-red-50 text-red-700 px-3 py-2 rounded-lg text-sm mb-3">
                {rotateError}
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowRotateModal(false)}
                className="px-4 py-2 text-sm text-slate-700 border border-slate-200 rounded-lg hover:bg-slate-50"
                disabled={rotating}
              >
                Cancel
              </button>
              <button
                onClick={onRotate}
                disabled={rotating}
                className="px-4 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-700 disabled:opacity-50"
              >
                {rotating ? "Rotating…" : "Rotate"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
