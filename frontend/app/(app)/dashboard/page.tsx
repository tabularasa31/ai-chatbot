"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export default function DashboardPage() {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [hasOpenaiKey, setHasOpenaiKey] = useState(false);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [docCount, setDocCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copiedApiKey, setCopiedApiKey] = useState(false);
  const [copiedEmbed, setCopiedEmbed] = useState(false);
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [openaiKeySaving, setOpenaiKeySaving] = useState(false);
  const [openaiKeyError, setOpenaiKeyError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [user, clientOrNull] = await Promise.all([
          api.auth.getMe(),
          api.clients.getMe().catch(() => null),
        ]);
        setUserEmail(user.email);

        let client = clientOrNull;
        if (!client) {
          try {
            client = await api.clients.create("My Workspace");
          } catch (err) {
            const msg = err instanceof Error ? err.message : "";
            if (msg.includes("already exists") || msg.includes("409")) {
              client = await api.clients.getMe();
            } else {
              setError(msg || "Failed to create client");
              return;
            }
          }
        }
        setApiKey(client.api_key);
        setHasOpenaiKey(client.has_openai_key ?? false);

        const docs = await api.documents.list();
        setDocCount(docs.length);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function saveOpenaiKey() {
    setOpenaiKeyError("");
    const key = openaiKeyInput.trim();
    if (!key) return;
    if (!key.startsWith("sk-")) {
      setOpenaiKeyError("OpenAI API key must start with 'sk-'");
      return;
    }
    setOpenaiKeySaving(true);
    try {
      await api.clients.update({ openai_api_key: key });
      setHasOpenaiKey(true);
      setOpenaiKeyInput("");
    } catch (err) {
      setOpenaiKeyError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setOpenaiKeySaving(false);
    }
  }

  async function removeOpenaiKey() {
    setOpenaiKeyError("");
    setOpenaiKeySaving(true);
    try {
      await api.clients.update({ openai_api_key: null });
      setHasOpenaiKey(false);
      setOpenaiKeyInput("");
    } catch (err) {
      setOpenaiKeyError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setOpenaiKeySaving(false);
    }
  }

  function copyApiKey() {
    if (apiKey) {
      navigator.clipboard.writeText(apiKey);
      setCopiedApiKey(true);
      setTimeout(() => setCopiedApiKey(false), 2000);
    }
  }

  function copyEmbedCode() {
    const code =
      "<script src=\"" +
      API_URL +
      "/embed.js\"></script>\n<div id=\"ai-chat-widget\" data-api-key=\"" +
      apiKey +
      "\"></div>";
    navigator.clipboard.writeText(code);
    setCopiedEmbed(true);
    setTimeout(() => setCopiedEmbed(false), 2000);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-600">Loading...</div>
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
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Dashboard</h1>
        {userEmail && (
          <p className="text-slate-600 text-sm mt-1">{userEmail}</p>
        )}
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-lg font-medium text-slate-800 mb-2">
          Welcome{userEmail ? ", " + userEmail : ""}!
        </h2>
        <p className="text-slate-600 mb-4">Your API Key:</p>
        <div className="flex items-center gap-2 flex-wrap">
          <code className="flex-1 min-w-0 px-3 py-2 bg-slate-100 rounded-md text-sm text-slate-800 break-all">
            {apiKey}
          </code>
          <button
            onClick={copyApiKey}
            className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700"
          >
            {copiedApiKey ? "Copied!" : "Copy"}
          </button>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-lg font-medium text-slate-800 mb-2">OpenAI API Key</h2>
        {!hasOpenaiKey ? (
          <>
            <div className="bg-amber-50 border border-amber-200 text-amber-800 px-4 py-3 rounded-lg mb-4">
              ⚠️ Add your OpenAI API key to enable chat
            </div>
            <input
              type="password"
              placeholder="sk-..."
              value={openaiKeyInput}
              onChange={(e) => setOpenaiKeyInput(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-slate-800 mb-2"
            />
            <button
              onClick={saveOpenaiKey}
              disabled={openaiKeySaving || !openaiKeyInput.trim()}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {openaiKeySaving ? "Saving..." : "Save"}
            </button>
          </>
        ) : (
          <>
            <p className="text-green-600 mb-2">✅ API key configured</p>
            <input
              type="password"
              placeholder="sk-..."
              value={openaiKeyInput}
              onChange={(e) => setOpenaiKeyInput(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-slate-800 mb-2"
            />
            <div className="flex gap-2">
              <button
                onClick={saveOpenaiKey}
                disabled={openaiKeySaving || !openaiKeyInput.trim()}
                className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {openaiKeySaving ? "Saving..." : "Update key"}
              </button>
              <button
                onClick={removeOpenaiKey}
                disabled={openaiKeySaving}
                className="px-4 py-2 bg-slate-200 text-slate-700 text-sm font-medium rounded-md hover:bg-slate-300 disabled:opacity-50"
              >
                Remove key
              </button>
            </div>
          </>
        )}
        {openaiKeyError && (
          <p className="text-red-600 text-sm mt-2">{openaiKeyError}</p>
        )}
        <p className="text-slate-500 text-xs mt-2">
          Your key is used for embeddings and chat. Get yours at platform.openai.com
        </p>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-lg font-medium text-slate-800 mb-2">Embed code</h2>
        <p className="text-slate-600 mb-4 text-sm">
          Add this to your website to embed the AI chat widget:
        </p>
        <pre className="bg-slate-100 p-4 rounded-md text-sm text-slate-800 overflow-x-auto mb-4">
          {"<script src=\"" + API_URL + "/embed.js\"></script>\n<div id=\"ai-chat-widget\" data-api-key=\"" + apiKey + "\"></div>"}
        </pre>
        <button
          onClick={copyEmbedCode}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700"
        >
          {copiedEmbed ? "Copied!" : "Copy embed code"}
        </button>
      </div>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-lg font-medium text-slate-800 mb-4">Quick links</h2>
        <div className="flex flex-wrap gap-4">
          <Link
            href="/documents"
            className="inline-flex items-center gap-2 text-blue-600 hover:underline"
          >
            Documents ({docCount ?? 0})
          </Link>
          <Link
            href="/logs"
            className="inline-flex items-center gap-2 text-blue-600 hover:underline"
          >
            Chat logs
          </Link>
        </div>
      </div>
    </div>
  );
}
