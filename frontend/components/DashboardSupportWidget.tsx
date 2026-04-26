"use client";

import { useEffect, useRef, useState } from "react";
import { MessageCircle, X } from "lucide-react";
import { ChatWidget } from "./ChatWidget";

const BOT_ID = process.env.NEXT_PUBLIC_CHAT9_BOT_ID;
const API_KEY = process.env.NEXT_PUBLIC_CHAT9_API_KEY;

export function DashboardSupportWidget() {
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [identityToken, setIdentityToken] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!BOT_ID || !API_KEY) return;

    fetch("/api/widget-identity", { credentials: "include" })
      .then((r) => r.json())
      .then((data: { identity_token?: string }) => {
        if (data.identity_token) setIdentityToken(data.identity_token);
      })
      .catch(() => {/* fall through to anonymous */});
  }, []);

  const handleToggle = () => {
    if (!open) setEverOpened(true);
    setOpen((v) => !v);
  };

  if (!BOT_ID || !API_KEY) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      {everOpened && (
        <div
          ref={panelRef}
          style={{ display: open ? undefined : "none" }}
          className="w-[380px] h-[560px] rounded-2xl shadow-2xl border border-gray-200 overflow-hidden flex flex-col bg-white"
        >
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
