"use client";

import { useRef, useSyncExternalStore, type KeyboardEvent } from "react";
import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import {
  SCHEMES,
  SCHEME_CHANGE_EVENT,
  readSchemeCookie,
  writeSchemeCookie,
  type Scheme,
} from "@/lib/scheme";
import { cn } from "./utils";

const OPTIONS: Record<Scheme, { label: string; Icon: LucideIcon }> = {
  system: { label: "System", Icon: Monitor },
  light: { label: "Light", Icon: Sun },
  dark: { label: "Dark", Icon: Moon },
};

const ARROW_STEP: Record<string, number> = {
  ArrowRight: 1,
  ArrowDown: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
};

function getServerScheme(): Scheme {
  return "system";
}

function subscribe(onChange: () => void) {
  window.addEventListener(SCHEME_CHANGE_EVENT, onChange);
  return () => window.removeEventListener(SCHEME_CHANGE_EVENT, onChange);
}

export function ThemeSwitch({ className }: { className?: string }) {
  const selected = useSyncExternalStore(subscribe, readSchemeCookie, getServerScheme);
  const buttons = useRef<Record<Scheme, HTMLButtonElement | null>>({
    system: null,
    light: null,
    dark: null,
  });

  function select(scheme: Scheme) {
    writeSchemeCookie(scheme);
    buttons.current[scheme]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const step = ARROW_STEP[event.key];
    if (step === undefined) return;
    event.preventDefault();
    select(SCHEMES[(SCHEMES.indexOf(selected) + step + SCHEMES.length) % SCHEMES.length]);
  }

  return (
    <div
      role="radiogroup"
      aria-label="Colour scheme"
      className={cn("inline-flex rounded-control border border-line", className)}
    >
      {SCHEMES.map((scheme) => {
        const { label, Icon } = OPTIONS[scheme];
        const checked = scheme === selected;
        return (
          <button
            key={scheme}
            ref={(node) => {
              buttons.current[scheme] = node;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={label}
            title={label}
            tabIndex={checked ? 0 : -1}
            onClick={() => select(scheme)}
            onKeyDown={onKeyDown}
            className={cn(
              "flex h-11 w-11 items-center justify-center first:rounded-l-control last:rounded-r-control",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
              checked
                ? "bg-brand-subtle text-brand"
                : "text-fg-muted hover:bg-surface-alt active:bg-surface-alt",
            )}
          >
            <Icon size={16} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}
