"use client";

import Link from "next/link";
import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "./utils";

export type SectionTab<T extends string> = { id: T; label: string; suffix?: ReactNode };
export type SectionLinkTab<T extends string> = SectionTab<T> & { href: string };

type SectionTabsProps<T extends string> = {
  label: string;
  activeId: T;
} & (
  | { tabs: readonly SectionLinkTab<T>[]; onChange?: undefined }
  | { tabs: readonly SectionTab<T>[]; onChange: (id: T) => void; panelId: string }
);

export function sectionTabId(panelId: string, id: string) {
  return `${panelId}-tab-${id}`;
}

const TAB_CLASS =
  "inline-flex h-11 items-center gap-2 rounded-control px-4 text-ui font-medium " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
const SELECTED_CLASS = "bg-brand-subtle text-brand";
const IDLE_CLASS = "text-fg-muted hover:bg-surface-alt active:bg-surface-alt";
const ROW_CLASS = "flex flex-wrap gap-1";

const ARROW_STEP: Partial<Record<string, 1 | -1>> = { ArrowRight: 1, ArrowLeft: -1 };

function tabClass(selected: boolean) {
  return cn(TAB_CLASS, selected ? SELECTED_CLASS : IDLE_CLASS);
}

export function SectionTabs<T extends string>(props: SectionTabsProps<T>) {
  if (props.onChange === undefined) {
    return <LinkTabs label={props.label} activeId={props.activeId} tabs={props.tabs} />;
  }
  return (
    <ButtonTabs
      label={props.label}
      activeId={props.activeId}
      tabs={props.tabs}
      onChange={props.onChange}
      panelId={props.panelId}
    />
  );
}

function LinkTabs<T extends string>({
  label,
  activeId,
  tabs,
}: {
  label: string;
  activeId: T;
  tabs: readonly SectionLinkTab<T>[];
}) {
  return (
    <nav aria-label={label} className={ROW_CLASS}>
      {tabs.map((tab) => {
        const selected = tab.id === activeId;
        return (
          <Link
            key={tab.id}
            href={tab.href}
            aria-current={selected ? "page" : undefined}
            className={tabClass(selected)}
          >
            {tab.label}
            {tab.suffix}
          </Link>
        );
      })}
    </nav>
  );
}

function ButtonTabs<T extends string>({
  label,
  activeId,
  tabs,
  onChange,
  panelId,
}: {
  label: string;
  activeId: T;
  tabs: readonly SectionTab<T>[];
  onChange: (id: T) => void;
  panelId: string;
}) {
  const buttons = useRef<Partial<Record<T, HTMLButtonElement | null>>>({});

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = tabs.findIndex((tab) => tab.id === activeId);
    const step = ARROW_STEP[event.key];
    let next: number;
    if (step !== undefined) next = (index + step + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    const target = tabs[next].id;
    onChange(target);
    buttons.current[target]?.focus();
  }

  return (
    <div role="tablist" aria-label={label} className={ROW_CLASS}>
      {tabs.map((tab) => {
        const selected = tab.id === activeId;
        return (
          <button
            key={tab.id}
            ref={(node) => {
              buttons.current[tab.id] = node;
            }}
            id={sectionTabId(panelId, tab.id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={onKeyDown}
            className={tabClass(selected)}
          >
            {tab.label}
            {tab.suffix}
          </button>
        );
      })}
    </div>
  );
}
