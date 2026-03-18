"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export default function DashboardPage() {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [docCount, setDocCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copiedApiKey, setCopiedApiKey] = useState(false);
  const [copiedEmbed, setCopiedEmbed] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        let client = await api.clients.getMe().catch(() => null);
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
      <h1 className="text-2xl font-semibold text-slate-800">Dashboard</h1>

      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-lg font-medium text-slate-800 mb-2">Welcome!</h2>
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
