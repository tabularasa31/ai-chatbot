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

const ARROW_STEP: Partial<Record<string, 1 | -1>> = {
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

const SURFACES = {
  canvas: {
    group: "border-line",
    segment: "focus-visible:ring-focus",
    checked: "bg-brand-subtle text-brand",
    unchecked: "text-fg-muted hover:bg-surface-alt active:bg-surface-alt",
  },
  chrome: {
    group: "border-chrome-line-strong",
    segment: "focus-visible:ring-chrome-accent",
    checked: "bg-chrome-active text-chrome-accent",
    unchecked: "text-chrome-fg-muted hover:bg-chrome-hover active:bg-chrome-hover",
  },
} as const;

export function ThemeSwitch({
  className,
  surface = "canvas",
}: {
  className?: string;
  surface?: keyof typeof SURFACES;
}) {
  const styles = SURFACES[surface];
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
      className={cn("inline-flex rounded-control border", styles.group, className)}
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
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset",
              styles.segment,
              checked ? styles.checked : styles.unchecked,
            )}
          >
            <Icon size={16} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}
