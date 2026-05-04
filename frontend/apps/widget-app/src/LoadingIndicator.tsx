"use client";

import { useEffect, useState } from "react";

const PHASE_LABELS: Record<string, string> = {
  thinking: "Looking it up",
  searching: "Reading sources",
  reasoning: "Thinking it through",
  writing: "Writing answer",
};

const FALLBACK_LABEL = PHASE_LABELS.thinking;
const STALLED_LABEL = "Still working on it";
// Safety-net: if a stage stays the same for this long (and no chunks have
// arrived to replace the indicator), swap the label so the user doesn't
// perceive a freeze. Covers reasoning models that go silent before the
// first <thought> tag arrives, or any other long pre-token gap.
const STALLED_AFTER_MS = 8000;

export function LoadingIndicator({ stage }: { stage: string | null }) {
  const [stalled, setStalled] = useState(false);
  // Until the backend yields a stage, show the first phase as a default.
  // When stage changes, retype it (gives a "bot switched step" cue).
  const phaseLabel =
    (stage && PHASE_LABELS[stage]) ? PHASE_LABELS[stage] : FALLBACK_LABEL;
  const target = stalled ? STALLED_LABEL : phaseLabel;

  const [text, setText] = useState("");
  const [showCaret, setShowCaret] = useState(true);

  useEffect(() => {
    setStalled(false);
    const id = setTimeout(() => setStalled(true), STALLED_AFTER_MS);
    return () => clearTimeout(id);
  }, [stage]);

  useEffect(() => {
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    setText("");
    let i = 0;
    const tick = () => {
      if (i <= target.length) {
        setText(target.slice(0, i));
        i++;
        timeoutId = setTimeout(tick, 40 + Math.random() * 35);
      }
    };
    tick();
    return () => {
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    };
  }, [target]);

  useEffect(() => {
    const id = setInterval(() => setShowCaret((c) => !c), 480);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center gap-2.5 px-1 py-1" role="status" aria-live="polite">
      <Spark />
      <span
        className="inline-flex items-center text-[12.5px] tracking-[0.2px] text-[#A8A3B8]"
        style={{ fontVariantLigatures: "none", minHeight: 18 }}
      >
        {text}
        <span
          aria-hidden
          className="ml-0.5 inline-block align-middle"
          style={{
            width: 1.5,
            height: 12,
            background: "#9D8FCF",
            opacity: showCaret ? 1 : 0,
            transition: "opacity 0.05s",
          }}
        />
      </span>
    </div>
  );
}

function Spark() {
  return (
    <span
      aria-hidden
      className="inline-flex"
      style={{
        width: 16,
        height: 16,
        animation:
          "loaderSpin 3.6s cubic-bezier(.55,.1,.45,.9) infinite, loaderBreathe 2.4s ease-in-out infinite",
        transformOrigin: "center",
      }}
    >
      <svg
        viewBox="0 0 24 24"
        width={16}
        height={16}
        fill="none"
        style={{ overflow: "visible" }}
      >
        <g stroke="#9D8FCF" strokeWidth="1.4" strokeLinecap="round">
          <line x1="12" y1="3" x2="12" y2="21" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="5.6" y1="5.6" x2="18.4" y2="18.4" />
          <line x1="18.4" y1="5.6" x2="5.6" y2="18.4" />
        </g>
        <circle cx="12" cy="12" r="1.1" fill="#9D8FCF" />
      </svg>
      <style>{`
        @keyframes loaderSpin    { to { transform: rotate(360deg); } }
        @keyframes loaderBreathe { 0%,100% { filter: none; } 50% { filter: brightness(1.15); } }
      `}</style>
    </span>
  );
}
