"use client";

import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";

const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ||
  (typeof window !== "undefined" ? window.location.origin : "");
const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

type Mode = "bubble" | "inline";

function EmbedContent() {
  const [publicId, setPublicId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<Mode>("bubble");
  const [color, setColor] = useState("#a855f7");
  const [position, setPosition] = useState<"right" | "left">("right");
  const [targetId, setTargetId] = useState("chat9-widget");

  useEffect(() => {
    api.bots
      .list()
      .then((bots) => {
        const firstActive = bots.find((b) => b.is_active) ?? bots[0];
        setPublicId(firstActive?.public_id ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  // The dashboard origin where /widget/* and /api/widget-* live; only emitted
  // when it differs from the production default the loader bakes in.
  const apiBaseAttr =
    APP_URL && APP_URL !== "https://getchat9.live"
      ? `  data-api-base="${APP_URL}"`
      : null;

  function getBubbleSnippet() {
    const attrs = [
      `  src="${WIDGET_LOADER_URL}"`,
      `  data-bot-id="${publicId ?? "YOUR_BOT_ID"}"`,
      ...(apiBaseAttr ? [apiBaseAttr] : []),
      ...(color !== "#a855f7" ? [`  data-color="${color}"`] : []),
      ...(position !== "right" ? [`  data-position="${position}"`] : []),
    ];
    return `<script\n${attrs.join("\n")}>\n</script>`;
  }

  function getInlineSnippet() {
    const divPart = `<div id="${targetId}"></div>`;
    const attrs = [
      `  src="${WIDGET_LOADER_URL}"`,
      `  data-bot-id="${publicId ?? "YOUR_BOT_ID"}"`,
      ...(apiBaseAttr ? [apiBaseAttr] : []),
      `  data-mode="inline"`,
      `  data-target="${targetId}"`,
    ];
    return `${divPart}\n<script\n${attrs.join("\n")}>\n</script>`;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg">{error}</div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Embed your bot</h1>
        <p className="text-slate-500 text-sm mt-1">
          Copy one snippet into your website — no developer setup needed.
        </p>
      </div>

      {/* Mode tabs */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-5">
        <div className="flex gap-3">
          <button
            onClick={() => setMode("bubble")}
            className={`flex-1 rounded-lg border-2 p-4 text-left transition-colors ${
              mode === "bubble"
                ? "border-violet-500 bg-violet-50"
                : "border-slate-200 hover:border-slate-300"
            }`}
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 text-xl">💬</span>
              <div>
                <p className={`text-sm font-semibold ${mode === "bubble" ? "text-violet-700" : "text-slate-700"}`}>
                  Chat Bubble
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Floating button in the corner. Opens on click.
                </p>
                <p className="text-xs text-slate-400 mt-1">
                  Good for: support on any page
                </p>
              </div>
            </div>
          </button>

          <button
            onClick={() => setMode("inline")}
            className={`flex-1 rounded-lg border-2 p-4 text-left transition-colors ${
              mode === "inline"
                ? "border-violet-500 bg-violet-50"
                : "border-slate-200 hover:border-slate-300"
            }`}
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 text-xl">🪟</span>
              <div>
                <p className={`text-sm font-semibold ${mode === "inline" ? "text-violet-700" : "text-slate-700"}`}>
                  Inline Widget
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Embedded inside a container on the page.
                </p>
                <p className="text-xs text-slate-400 mt-1">
                  Good for: docs, help centers, landing pages
                </p>
              </div>
            </div>
          </button>
        </div>

        {/* Bubble options */}
        {mode === "bubble" && (
          <div className="grid grid-cols-2 gap-4 pt-1">
            <div>
              <label htmlFor="widget-color" className="block text-xs font-medium text-slate-600 mb-1.5">
                Button color
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="widget-color"
                  type="color"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-8 w-14 cursor-pointer rounded border border-slate-200 p-0.5"
                />
                <code className="text-xs text-slate-500 font-mono">{color}</code>
              </div>
            </div>
            <div>
              <p className="block text-xs font-medium text-slate-600 mb-1.5">
                Position
              </p>
              <div className="flex gap-2">
                {(["right", "left"] as const).map((p) => (
                  <button
                    key={p}
                    onClick={() => setPosition(p)}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                      position === p
                        ? "bg-violet-600 text-white border-violet-600"
                        : "border-slate-200 text-slate-600 hover:border-slate-300"
                    }`}
                  >
                    {p === "right" ? "Bottom right" : "Bottom left"}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Inline options */}
        {mode === "inline" && (
          <div className="pt-1">
            <label htmlFor="inline-target-id" className="block text-xs font-medium text-slate-600 mb-1.5">
              Container element ID
            </label>
            <input
              id="inline-target-id"
              type="text"
              value={targetId}
              onChange={(e) => setTargetId(e.target.value.replace(/[^a-zA-Z0-9-_]/g, "-"))}
              className="px-3 py-1.5 border border-slate-200 rounded-md text-sm font-mono text-slate-700 w-60 focus:outline-none focus:ring-2 focus:ring-violet-300"
              placeholder="chat9-widget"
            />
            <p className="text-xs text-slate-400 mt-1.5">
              ID of the {"<div>"} that will contain the chat widget.
            </p>
          </div>
        )}
      </div>

      {/* Snippet */}
      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h2 className="text-base font-semibold text-slate-800 mb-1">
          {mode === "bubble" ? "Add to your website" : "Add to your page"}
        </h2>
        <p className="text-slate-500 text-sm mb-3">
          {mode === "bubble"
            ? <>Paste before <code className="bg-slate-100 px-1 rounded">&lt;/body&gt;</code> on every page where you want the chat button.</>
            : <>Place the <code className="bg-slate-100 px-1 rounded">&lt;div&gt;</code> where you want the widget to appear, then paste the script anywhere after it.</>
          }
        </p>
        <CodeBlockWithCopy
          code={mode === "bubble" ? getBubbleSnippet() : getInlineSnippet()}
          copyLabel="Copy snippet"
          tone="light"
          preClassName="text-sm"
        />

        {/* Attribute reference */}
        <div className="mt-5 border-t border-slate-100 pt-4">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Available attributes
          </p>
          <div className="space-y-1.5 text-xs text-slate-500">
            {mode === "bubble" ? (
              <>
                <div><code className="font-mono text-slate-700">data-bot-id</code> — your bot&apos;s public ID (required)</div>
                <div><code className="font-mono text-slate-700">data-color</code> — button color, e.g. <code className="font-mono">#a855f7</code> (optional)</div>
                <div><code className="font-mono text-slate-700">data-position</code> — <code className="font-mono">right</code> or <code className="font-mono">left</code> (default: right)</div>
              </>
            ) : (
              <>
                <div><code className="font-mono text-slate-700">data-bot-id</code> — your bot&apos;s public ID (required)</div>
                <div><code className="font-mono text-slate-700">data-mode</code> — must be <code className="font-mono">inline</code></div>
                <div><code className="font-mono text-slate-700">data-target</code> — ID of the container element (required)</div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function EmbedPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-16">
          <div className="animate-pulse text-slate-500 text-sm">Loading…</div>
        </div>
      }
    >
      <EmbedContent />
    </Suspense>
  );
}
