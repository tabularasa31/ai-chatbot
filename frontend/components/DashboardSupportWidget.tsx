"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircle, Minimize2, X } from "lucide-react";
import { ChatWidget } from "./ChatWidget";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;
const API_KEY = process.env.NEXT_PUBLIC_CHAT9_API_KEY;

const MIN_W = 300;
const MAX_W = 700;
const MIN_H = 400;
const MAX_H = 860;
const DEFAULT_W = 380;
const DEFAULT_H = 560;

// Pixels the panel must stay away from viewport edges.
const BOTTOM_CLEARANCE = 100; // bottom-6 (24) + button h-14 (56) + gap-3 (12) + margin (8)
const SIDE_CLEARANCE = 32;    // right-6 (24) + margin (8)

export function DashboardSupportWidget() {
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [identityToken, setIdentityToken] = useState<string | null>(null);
  const [size, setSize] = useState({ w: DEFAULT_W, h: DEFAULT_H });
  const isResized = size.w !== DEFAULT_W || size.h !== DEFAULT_H;
  const panelRef = useRef<HTMLDivElement>(null);
  const dragStart = useRef<{ x: number; y: number; w: number; h: number } | null>(null);

  useEffect(() => {
    if (!BOT_ID || !API_KEY) return;

    fetch("/api/widget-identity", { credentials: "include" })
      .then((r) => r.json())
      .then((data: { identity_token?: string }) => {
        if (data.identity_token) setIdentityToken(data.identity_token);
      })
      .catch(() => {/* fall through to anonymous */});
  }, []);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!dragStart.current) return;
    const dx = dragStart.current.x - e.clientX;
    const dy = dragStart.current.y - e.clientY;
    // Clamp against static limits AND the live viewport so the panel can't
    // slide under the navbar or off the left edge.
    const vpMaxW = Math.min(MAX_W, window.innerWidth - SIDE_CLEARANCE);
    const vpMaxH = Math.min(MAX_H, window.innerHeight - BOTTOM_CLEARANCE);
    setSize({
      w: Math.min(vpMaxW, Math.max(MIN_W, dragStart.current.w + dx)),
      h: Math.min(vpMaxH, Math.max(MIN_H, dragStart.current.h + dy)),
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

  if (!BOT_ID || !API_KEY) return null;

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

          {/* Reset-size button — visible in header area whenever panel is non-default size */}
          {isResized && (
            <button
              type="button"
              aria-label="Reset chat window to default size"
              onClick={() => setSize({ w: DEFAULT_W, h: DEFAULT_H })}
              title="Reset size"
              className="absolute top-3 right-3 z-10 flex items-center justify-center w-7 h-7 rounded-full bg-white/20 hover:bg-white/40 text-white transition-colors"
            >
              <Minimize2 size={14} />
            </button>
          )}

          <ChatWidget
            botId={BOT_ID}
            apiKey={API_KEY}
            identityToken={identityToken}
            isOpen={open}
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
