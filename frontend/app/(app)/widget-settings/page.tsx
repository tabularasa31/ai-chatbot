"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type BotResponse, type KycSecretResponse, type KycStatusResponse } from "@/lib/api";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";

export default function WidgetSettingsPage() {
  const [defaultBot, setDefaultBot] = useState<BotResponse | null>(null);
  const [status, setStatus] = useState<KycStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copiedBotId, setCopiedBotId] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const [s, bots] = await Promise.all([
        api.kyc.getStatus(),
        api.bots.list(),
      ]);
      setStatus(s);
      setDefaultBot(bots[0] ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate() {
    setBusy(true);
    setOneTimeSecret(null);
    setError("");
    try {
      const res: KycSecretResponse = await api.kyc.generateSecret();
      setOneTimeSecret(res.secret_key);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to generate secret");
    } finally {
      setBusy(false);
    }
  }

  async function handleRotate() {
    setBusy(true);
    setOneTimeSecret(null);
    setError("");
    try {
      const res: KycSecretResponse = await api.kyc.rotateSecret();
      setOneTimeSecret(res.secret_key);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to rotate secret");
    } finally {
      setBusy(false);
    }
  }

  async function copySecret() {
    if (!oneTimeSecret) return;
    await navigator.clipboard.writeText(oneTimeSecret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function copyBotId() {
    if (!defaultBot?.public_id) return;
    await navigator.clipboard.writeText(defaultBot.public_id);
    setCopiedBotId(true);
    setTimeout(() => setCopiedBotId(false), 2000);
  }

  const nodeSnippet = `const crypto = require("crypto");

function makeWidgetIdentityToken({
  secretHex,
  userId,
  extras = {},
  ttlSeconds = 300,
}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    user_id: userId,
    exp: now + ttlSeconds,
    iat: now,
    ...extras,
  };
  const sorted = {};
  for (const key of Object.keys(payload).sort()) {
    sorted[key] = payload[key];
  }
  const json = JSON.stringify(sorted);
  const b64 = Buffer.from(json, "utf8")
    .toString("base64")
    .replace(/[+]/g, "-")
    .replace(/[/]/g, "_")
    .replace(/=+$/, "");
  const sig = crypto
    .createHmac("sha256", Buffer.from(secretHex, "utf8"))
    .update(b64)
    .digest("hex");
  return \`\${b64}.\${sig}\`;
}`;

  if (loading) {
    return <p className="text-slate-600">Loading…</p>;
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Widget</h1>
        <p className="mt-1 text-slate-500 text-sm">
          Integration settings for embed identity, bot ID, and install snippets.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 text-red-800 px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Bot ID</h2>
          <p className="text-slate-500 text-sm mt-1">
            Use this public client ID in widget session init and integration payloads.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-xs break-all bg-slate-50 border border-slate-200 text-slate-900 font-mono rounded px-3 py-2 flex-1 min-w-0">
            {defaultBot?.public_id ?? "—"}
          </code>
          <button
            type="button"
            onClick={copyBotId}
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm shrink-0"
          >
            {copiedBotId ? "Copied" : "Copy"}
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
        <h2 className="text-base font-semibold text-slate-800">Identified mode</h2>
        <p className="text-slate-500 text-sm">
          Status:{" "}
          <strong>
            {status?.has_secret
              ? "Configured — you can issue signing secrets and pass tokens at session init"
              : "Not configured — generate a signing secret to enable identified sessions"}
          </strong>
        </p>
        {status?.masked_secret_hint && (
          <p className="text-sm text-slate-500">
            Stored secret: <code className="text-slate-700">{status.masked_secret_hint}</code>
          </p>
        )}
        <div className="flex flex-wrap gap-3">
          {!status?.has_secret ? (
            <button
              type="button"
              disabled={busy}
              onClick={handleGenerate}
              className="rounded-lg bg-violet-600 text-white px-4 py-2 text-sm font-medium hover:bg-violet-700 disabled:opacity-50"
            >
              Generate signing secret
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={handleRotate}
              className="rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 px-4 py-2 text-sm font-medium disabled:opacity-50 transition-colors"
            >
              Rotate secret
            </button>
          )}
        </div>
      </section>

      {oneTimeSecret && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-6 space-y-3">
          <p className="text-amber-900 text-sm font-medium">
            Store this securely. It will not be shown again.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="text-xs break-all bg-white border border-amber-200 text-slate-900 font-mono rounded px-2 py-1 flex-1 min-w-0">
              {oneTimeSecret}
            </code>
            <button
              type="button"
              onClick={copySecret}
              className="rounded-lg bg-amber-800 text-white px-3 py-1.5 text-sm shrink-0"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-3">
        <h2 className="text-base font-semibold text-slate-800">Integration health (7 days)</h2>
        <ul className="text-sm text-slate-600 space-y-1">
          <li>
            Identified session rate:{" "}
            <strong>{((status?.identified_session_rate_7d ?? 0) * 100).toFixed(1)}%</strong>
          </li>
          <li>
            Last identified session:{" "}
            <strong>
              {status?.last_identified_session
                ? new Date(status.last_identified_session).toLocaleString()
                : "—"}
            </strong>
          </li>
        </ul>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-3">
        <h2 className="text-base font-semibold text-slate-800">Server-side token (Node.js)</h2>
        <p className="text-slate-500 text-sm">
          Call <code className="text-slate-800">POST /widget/session/init</code> with{" "}
          <code className="text-slate-800">api_key</code> and optional{" "}
          <code className="text-slate-800">identity_token</code>. Use your{" "}
          <code className="text-slate-800">Bot ID</code> as{" "}
          <code className="text-slate-800">tenant_id</code> in the payload.
        </p>
        <CodeBlockWithCopy code={nodeSnippet} />
      </section>
    </div>
  );
}
