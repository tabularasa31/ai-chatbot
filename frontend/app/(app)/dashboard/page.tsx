"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, type ClientResponse } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ||
  (typeof window !== "undefined" ? window.location.origin : "");

function DashboardContent() {
  const searchParams = useSearchParams();
  const showVerificationBanner = searchParams.get("verification_sent") === "1";
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [publicId, setPublicId] = useState<string | null>(null);
  const [hasOpenaiKey, setHasOpenaiKey] = useState(false);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [emailNotVerified, setEmailNotVerified] = useState(false);
  const [copiedApiKey, setCopiedApiKey] = useState(false);
  const [copiedEmbed, setCopiedEmbed] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const [user, clientOrNull] = await Promise.all([
          api.auth.getMe(),
          api.clients.getMe().catch(() => null),
        ]);
        setUserEmail(user.email);

        let client: ClientResponse | null = clientOrNull;
        if (!client) {
          try {
            client = await api.clients.create("My Workspace");
          } catch (err) {
            const msg = err instanceof Error ? err.message : "";
            if (msg.includes("already exists") || msg.includes("409")) {
              client = await api.clients.getMe();
            } else if (msg.toLowerCase().includes("email not verified") || msg.includes("403")) {
              setEmailNotVerified(true);
              return;
            } else {
              setError(msg || "Failed to create client");
              return;
            }
          }
        }
        setApiKey(client.api_key);
        setPublicId(client.public_id ?? null);
        setHasOpenaiKey(client.has_openai_key ?? false);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to load";
        if (msg.toLowerCase().includes("email not verified") || msg.includes("403")) {
          setEmailNotVerified(true);
        } else {
          setError(msg);
        }
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  function copyApiKey() {
    if (apiKey) {
      navigator.clipboard.writeText(apiKey);
      setCopiedApiKey(true);
      setTimeout(() => setCopiedApiKey(false), 2000);
    }
  }

  function getEmbedSnippet() {
    const scriptUrl = `${API_URL}/embed.js?clientId=${encodeURIComponent(publicId ?? "")}`;
    if (APP_URL && APP_URL !== API_URL) {
      return `<script>window.Chat9Config={widgetUrl:"${APP_URL}"};</script>\n<script src="${scriptUrl}"></script>`;
    }
    return `<script src="${scriptUrl}"></script>`;
  }

  function copyEmbedCode() {
    navigator.clipboard.writeText(getEmbedSnippet());
    setCopiedEmbed(true);
    setTimeout(() => setCopiedEmbed(false), 2000);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    );
  }

  if (emailNotVerified) {
    return (
      <div className="max-w-md mx-auto mt-16 text-center space-y-4">
        <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center mx-auto">
          <svg className="w-7 h-7 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25H4.5a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5H4.5a2.25 2.25 0 00-2.25 2.25m19.5 0-9.75 6.75L2.25 6.75" />
          </svg>
        </div>
        <h2 className="text-lg font-semibold text-slate-800">Verify your email to continue</h2>
        <p className="text-slate-500 text-sm">
          We sent a verification link to{userEmail ? <> <span className="font-medium text-slate-700">{userEmail}</span></> : " your email"}.
          Click the link in the email to activate your account.
        </p>
        <p className="text-slate-400 text-xs">Didn&apos;t get it? Check your spam folder.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {showVerificationBanner && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded-lg text-sm">
          We sent a verification link to your email. Please check your inbox and click the link to verify your account.
        </div>
      )}
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Dashboard</h1>
        {userEmail && (
          <p className="text-slate-500 text-sm mt-1">{userEmail}</p>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h2 className="text-base font-semibold text-slate-800 mb-1">Your API Key</h2>
        <p className="text-slate-500 text-sm mb-3">Use this key to authenticate API requests.</p>
        <div className="flex items-center gap-2 flex-wrap">
          <code className="flex-1 min-w-0 px-3 py-2 bg-slate-100 rounded-lg text-sm text-slate-800 break-all">
            {apiKey}
          </code>
          <button
            onClick={copyApiKey}
            className="px-4 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-700 transition-colors"
          >
            {copiedApiKey ? "Copied!" : "Copy"}
          </button>
        </div>
      </div>

      {!hasOpenaiKey && (
        <div className="bg-amber-50 border border-amber-100 text-amber-700 px-4 py-3 rounded-lg text-sm flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
          OpenAI API key is not set —{" "}
          <a href="/settings" className="underline font-medium">configure in Settings</a>
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h2 className="text-base font-semibold text-slate-800 mb-1">Embed code</h2>
        <p className="text-slate-500 text-sm mb-3">
          Add this snippet to your website HTML, right before{" "}
          <code className="bg-slate-100 px-1 rounded">&lt;/body&gt;</code>:
        </p>
        <pre className="bg-slate-100 p-4 rounded-lg text-sm text-slate-800 overflow-x-auto mb-3">
          {getEmbedSnippet()}
        </pre>
        <p className="text-slate-400 text-xs mb-4">
          One-line embed — works on any domain. No CORS setup needed.
        </p>
        <button
          onClick={copyEmbedCode}
          className="px-4 py-2 bg-violet-600 text-white text-sm font-medium rounded-lg hover:bg-violet-700 transition-colors"
        >
          {copiedEmbed ? "Copied!" : "Copy embed code"}
        </button>
      </div>

    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    }>
      <DashboardContent />
    </Suspense>
  );
}
