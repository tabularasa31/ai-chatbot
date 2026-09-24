import type { WindowWithChat9Widget } from "@/types/chat9-widget";

export const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

// Not lib/site.ts's SITE_URL: that defaults to the prod URL when
// NEXT_PUBLIC_APP_URL is unset, so on a preview deploy it would miss the
// override and the widget would call prod instead of this origin.
export function getApiBaseOverride(): string | null {
  const appUrl =
    process.env.NEXT_PUBLIC_APP_URL ||
    (typeof window !== "undefined" ? window.location.origin : "");
  return appUrl && appUrl !== "https://getchat9.live" ? appUrl : null;
}

// Snippet TEXT must stay byte-identical for a given set of options —
// dashboard/embed pages copy it verbatim into tenant websites.
export function buildEmbedSnippet(options: {
  botId: string;
  extraConfig?: Record<string, string>;
  extraHtml?: string;
  // "inline" is the dashboard's pre-existing one-line `{ apiBase: "…" }`
  // form; "multiline" (default) is the embed page's multi-key form.
  configFormat?: "inline" | "multiline";
}): string {
  const apiBaseOverride = getApiBaseOverride();
  const entries = Object.entries(options.extraConfig ?? {}).filter(([, v]) => v !== "");
  if (apiBaseOverride) entries.push(["apiBase", apiBaseOverride]);

  let configLiteral = "";
  if (entries.length > 0) {
    configLiteral =
      options.configFormat === "inline"
        ? `{ ${entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ")} }`
        : `{\n${entries.map(([k, v]) => `    ${k}: ${JSON.stringify(v)}`).join(",\n")}\n  }`;
  }

  const scriptTag = `<script\n  src="${WIDGET_LOADER_URL}"\n  data-bot-id="${options.botId}">\n</script>`;
  const startScript = `<script>\n  Chat9Widget.start(${configLiteral});\n</script>`;

  return [options.extraHtml, scriptTag, startScript].filter(Boolean).join("\n");
}

let scriptReady: Promise<void> | null = null;

// Injects the loader once per browser session; re-injects if a prior
// resolution went stale (e.g. after destroy() cleared window.Chat9Widget).
export function ensureLoaderScript(botId: string): Promise<void> {
  if (scriptReady && !(window as WindowWithChat9Widget).Chat9Widget) {
    scriptReady = null;
  }
  if (scriptReady) return scriptReady;
  if ((window as WindowWithChat9Widget).Chat9Widget) {
    scriptReady = Promise.resolve();
    return scriptReady;
  }
  scriptReady = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = WIDGET_LOADER_URL;
    script.async = true;
    script.setAttribute("data-bot-id", botId);
    script.onload = () => resolve();
    script.onerror = () => {
      scriptReady = null;
      reject(new Error("Chat9: failed to load widget.js"));
    };
    document.body.appendChild(script);
  });
  return scriptReady;
}
