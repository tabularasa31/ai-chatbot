"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  width?: string;
}

export function Tooltip({ content, children, className = "", width = "w-72" }: TooltipProps) {
  const triggerRef = useRef<HTMLSpanElement>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const show = useCallback(() => {
    if (triggerRef.current) {
      setRect(triggerRef.current.getBoundingClientRect());
    }
  }, []);

  const hide = useCallback(() => {
    setRect(null);
  }, []);

  const above = rect ? rect.top > window.innerHeight / 2 : false;

  const tooltipStyle: React.CSSProperties = rect
    ? {
        position: "fixed",
        zIndex: 9999,
        right: window.innerWidth - rect.right,
        ...(above
          ? { bottom: window.innerHeight - rect.top + 8 }
          : { top: rect.bottom + 8 }),
      }
    : {};

  /* eslint-disable jsx-a11y/no-noninteractive-tabindex */
  return (
    <span
      ref={triggerRef}
      className={`group relative inline-flex items-center gap-1.5 ${className}`}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      tabIndex={0}
    >
      {children}
      {mounted &&
        rect &&
        createPortal(
          <span
            className={`pointer-events-none ${width} rounded-lg bg-slate-900 px-3 py-2 text-left text-[11px] leading-5 text-white shadow-xl`}
            style={tooltipStyle}
          >
            {content}
          </span>,
          document.body,
        )}
    </span>
  );
  /* eslint-enable jsx-a11y/no-noninteractive-tabindex */
}
