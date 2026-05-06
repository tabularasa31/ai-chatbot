"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageCircle, X } from "lucide-react";
import { api } from "@/lib/api";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;
const WIDGET_BASE_URL =
  process.env.NEXT_PUBLIC_WIDGET_BASE_URL ?? "https://widget.getchat9.live/v1/";

const MIN_W = 300;
const MAX_W = 700;
const MIN_H = 400;
const MAX_H = 860;
const DEFAULT_W = 380;
const DEFAULT_H = 560;

// Pixels the panel must stay away from viewport edges.
const BOTTOM_CLEARANCE = 100; // bottom-6 (24) + button h-14 (56) + gap-3 (12) + margin (8)
const TOP_CLEARANCE = 56;     // fixed navbar h-12 (48) + margin (8)
const SIDE_CLEARANCE = 32;    // right-6 (24) + margin (8)

// Reduce a URL to its origin (`protocol://host`). Returns null for non-http(s)
// or unparseable input. Used to derive the postMessage targetOrigin from
// WIDGET_BASE_URL so we always pin handshake messages to the iframe's origin.
function originOf(raw: string): string | null {
  try {
    const u = new URL(raw);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    return u.origin;
  } catch {
    return null;
  }
}

export function DashboardSupportWidget() {
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [size, setSize] = useState({ w: DEFAULT_W, h: DEFAULT_H });
  const panelRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const dragStart = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  // Tracks whether the iframe has already announced chat9:ready. The handshake
  // is one-shot — if identity arrives after readiness, we replay it from this
  // ref instead of missing the window.
  const iframeReadyRef = useRef(false);

  const widgetOrigin = useMemo(() => originOf(WIDGET_BASE_URL), []);

  const iframeSrc = useMemo(() => {
    if (!BOT_ID) return null;
    if (typeof window === "undefined") return null;
    const url = new URL(WIDGET_BASE_URL);
    url.searchParams.set("botId", BOT_ID);
    url.searchParams.set("parentOrigin", window.location.origin);
    // The widget-app calls /widget/* and /api/widget-* on the dashboard origin;
    // CORS allowlist on dashboard middleware lets the cross-origin call through.
    url.searchParams.set("apiBase", window.location.origin);
    if (typeof navigator !== "undefined" && navigator.language) {
      url.searchParams.set("locale", navigator.language);
    }
    return url.toString();
  }, []);

  // Fetch the logged-in user's email so we can pass it as a userHint when the
  // iframe handshakes. undefined = fetch in-flight; null = no email available;
  // object = hints ready.
  type Hints = { email?: string };
  const [hints, setHints] = useState<Hints | null | undefined>(undefined);
  useEffect(() => {
    if (!BOT_ID) return;
    api.auth
      .getMe()
      .then((user) => {
        setHints(user.email ? { email: user.email } : null);
      })
      .catch(() => setHints(null));
  }, []);

  // Reply to chat9:ready handshakes from the iframe with userHints. The iframe
  // lives on widget.getchat9.live (cross-origin), so we use an explicit
  // targetOrigin. The two events (iframe ready / hints fetched) can land in
  // either order: register the listener as soon as we know widgetOrigin, and
  // re-attempt delivery whenever hints transition to a defined value.
  useEffect(() => {
    if (!widgetOrigin) return;

    const sendHints = (resolved: Hints | null | undefined) => {
      if (resolved === undefined) return;
      const target = iframeRef.current?.contentWindow;
      if (!target) return;
      const payload = resolved
        ? { type: "chat9:hints", userHints: resolved }
        : { type: "chat9:no-hints" };
      target.postMessage(payload, widgetOrigin);
    };

    function handleMessage(event: MessageEvent) {
      if (event.origin !== widgetOrigin) return;
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type !== "chat9:ready") return;
      iframeReadyRef.current = true;
      sendHints(hints);
    }
    window.addEventListener("message", handleMessage);

    // If the iframe announced readiness during a previous render (before hints
    // resolved), replay the response now that we have them.
    if (iframeReadyRef.current) {
      sendHints(hints);
    }

    return () => window.removeEventListener("message", handleMessage);
  }, [widgetOrigin, hints]);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!dragStart.current) return;
    const dx = dragStart.current.x - e.clientX;
    const dy = dragStart.current.y - e.clientY;
    // Clamp against static limits AND the live viewport so the panel can't
    // slide under the navbar or off the left edge.
    const vpMaxW = Math.min(MAX_W, window.innerWidth - SIDE_CLEARANCE);
    const vpMaxH = Math.min(MAX_H, window.innerHeight - BOTTOM_CLEARANCE - TOP_CLEARANCE);
    setSize({
      w: Math.round(Math.min(vpMaxW, Math.max(MIN_W, dragStart.current.w + dx))),
      h: Math.round(Math.min(vpMaxH, Math.max(MIN_H, dragStart.current.h + dy))),
    });
  }, []);

  const handleMouseUp = useCallback(() => {
    dragStart.current = null;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    window.removeEventListener("mousemove", handleMouseMove);
    window.removeEventListener("mouseup", handleMouseUp);
  }, [handleMouseMove]);

  const handleResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragStart.current = { x: e.clientX, y: e.clientY, w: size.w, h: size.h };
    document.body.style.userSelect = "none";
    document.body.style.cursor = "nwse-resize";
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  }, [size, handleMouseMove, handleMouseUp]);

  // Clean up drag listeners and body overrides if the component unmounts mid-drag.
  useEffect(() => {
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [handleMouseMove, handleMouseUp]);

  const handleToggle = () => {
    if (!open) {
      setEverOpened(true);
    } else {
      setSize({ w: DEFAULT_W, h: DEFAULT_H });
    }
    setOpen((v) => !v);
  };

  if (!BOT_ID || !iframeSrc) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      {everOpened && (
        <div
          ref={panelRef}
          style={{ display: open ? undefined : "none", width: size.w, height: size.h }}
          className="rounded-2xl shadow-2xl border border-gray-200 overflow-hidden flex flex-col bg-white relative"
        >
          {/* Resize handle — top-left corner */}
          <button
            type="button"
            aria-label="Drag to resize chat window"
            onMouseDown={handleResizeMouseDown}
            className="absolute top-0 left-0 z-10 w-5 h-5 cursor-nwse-resize group p-0 border-0 bg-transparent"
          >
            <svg
              viewBox="0 0 16 16"
              className="absolute top-1.5 left-1.5 w-3 h-3 text-white/50 group-hover:text-white/90 transition-colors"
              fill="currentColor"
            >
              <path d="M1 8a1 1 0 0 1 1-1h5a1 1 0 0 1 0 2H3.414l8.293 8.293a1 1 0 0 1-1.414 1.414L2 10.414V13a1 1 0 1 1-2 0V8Z" />
            </svg>
          </button>

          <iframe
            ref={iframeRef}
            src={iframeSrc}
            title="Chat9 support"
            allow="clipboard-write"
            className="block w-full h-full border-0"
          />
        </div>
      )}

      <button
        type="button"
        onClick={handleToggle}
        aria-label={open ? "Close support chat" : "Open support chat"}
        className="w-14 h-14 rounded-full bg-[#a855f7] hover:bg-[#9333ea] shadow-lg flex items-center justify-center text-white transition-colors"
      >
        {open ? <X size={22} /> : <MessageCircle size={22} />}
      </button>
    </div>
  );
}
