"use client";

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import { Check, Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import {
  SCHEMES,
  SCHEME_CHANGE_EVENT,
  readSchemeCookie,
  writeSchemeCookie,
  type Scheme,
} from "@/lib/scheme";
import { CHROME_PLATE } from "./button";
import { cn } from "./utils";

const OPTIONS: Record<Scheme, { label: string; Icon: LucideIcon }> = {
  system: { label: "System", Icon: Monitor },
  light: { label: "Light", Icon: Sun },
  dark: { label: "Dark", Icon: Moon },
};

const MENU_GAP_PX = 4;

type Side = "below" | "above";
type Align = "start" | "end";

function getServerScheme(): Scheme {
  return "system";
}

function subscribe(onChange: () => void) {
  window.addEventListener(SCHEME_CHANGE_EVENT, onChange);
  return () => window.removeEventListener(SCHEME_CHANGE_EVENT, onChange);
}

const SURFACES = {
  canvas: {
    visual:
      "text-fg-muted transition-[color,background-color,transform] group-hover:bg-surface-alt group-hover:text-fg group-active:scale-press-sm group-active:bg-line group-aria-expanded:bg-surface-alt group-aria-expanded:text-fg group-aria-expanded:group-active:bg-line group-focus-visible:ring-2 group-focus-visible:ring-focus group-focus-visible:ring-offset-2 group-focus-visible:ring-offset-canvas",
    menu: "border-line bg-surface dark:bg-surface-alt",
    item: "text-fg hover:bg-surface-alt focus-visible:bg-surface-alt focus-visible:ring-focus dark:hover:bg-canvas dark:focus-visible:bg-canvas",
    checked: "text-brand",
  },
  chrome: {
    visual: `${CHROME_PLATE} group-aria-expanded:bg-chrome-hover group-aria-expanded:text-chrome-fg group-aria-expanded:group-active:bg-chrome-press`,
    menu: "border-chrome-line bg-chrome",
    item: "text-chrome-fg hover:bg-chrome-hover focus-visible:bg-chrome-hover focus-visible:ring-chrome-accent",
    checked: "text-chrome-accent",
  },
} as const;

function fittingSide(trigger: DOMRect, menuHeight: number, preferred: Side): Side {
  const below = window.innerHeight - trigger.bottom;
  const above = trigger.top;
  const room = preferred === "below" ? below : above;
  const other = preferred === "below" ? above : below;
  if (room >= menuHeight + MENU_GAP_PX || room >= other) return preferred;
  return preferred === "below" ? "above" : "below";
}

function fittingAlign(trigger: DOMRect, menuWidth: number, preferred: Align): Align {
  const room = preferred === "start" ? window.innerWidth - trigger.left : trigger.right;
  if (room >= menuWidth) return preferred;
  return preferred === "start" ? "end" : "start";
}

export function ThemeMenu({
  className,
  surface = "canvas",
  side = "above",
  align = "start",
}: {
  className?: string;
  surface?: keyof typeof SURFACES;
  side?: Side;
  align?: Align;
}) {
  const styles = SURFACES[surface];
  const selected = useSyncExternalStore(subscribe, readSchemeCookie, getServerScheme);
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({ side, align });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const items = useRef<Record<Scheme, HTMLButtonElement | null>>({
    system: null,
    light: null,
    dark: null,
  });
  const { label: selectedLabel, Icon: SelectedIcon } = OPTIONS[selected];

  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return;
    const box = trigger.current.getBoundingClientRect();
    setPlacement({
      side: fittingSide(box, menu.current.offsetHeight, side),
      align: fittingAlign(box, menu.current.offsetWidth, align),
    });
    items.current[selected]?.focus();
  }, [open, side, align, selected]);

  useEffect(() => {
    if (!open) return;
    function closeOnOutsidePress(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutsidePress);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress);
  }, [open]);

  function closeToTrigger() {
    setOpen(false);
    trigger.current?.focus();
  }

  function choose(scheme: Scheme) {
    writeSchemeCookie(scheme);
    closeToTrigger();
  }

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowDown" || open) return;
    event.preventDefault();
    setOpen(true);
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = SCHEMES.findIndex((scheme) => items.current[scheme] === document.activeElement);
    const target = {
      ArrowDown: (current + 1) % SCHEMES.length,
      ArrowUp: (current - 1 + SCHEMES.length) % SCHEMES.length,
      Home: 0,
      End: SCHEMES.length - 1,
    }[event.key];
    if (target !== undefined) {
      event.preventDefault();
      items.current[SCHEMES[target]]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeToTrigger();
    } else if (event.key === "Tab") {
      trigger.current?.focus();
      setOpen(false);
    }
  }

  const accessibleName = `Colour scheme: ${selectedLabel}`;

  return (
    <div ref={root} className={cn("relative inline-block", className)}>
      <button
        ref={trigger}
        type="button"
        aria-label={accessibleName}
        title={accessibleName}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onTriggerKeyDown}
        className="group flex h-11 w-11 items-center justify-center focus-visible:outline-none"
      >
        <span
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-full",
            styles.visual,
          )}
        >
          <SelectedIcon size={16} aria-hidden />
        </span>
      </button>
      {open && (
        <div
          ref={menu}
          id={menuId}
          role="menu"
          aria-label="Colour scheme"
          tabIndex={-1}
          onKeyDown={onMenuKeyDown}
          className={cn(
            "absolute z-50 w-40 rounded-control border p-1 shadow-pop",
            placement.side === "below" ? "top-full -mt-0.5" : "bottom-full -mb-0.5",
            placement.align === "end" ? "right-1.5" : "left-1.5",
            styles.menu,
          )}
        >
          {SCHEMES.map((scheme) => {
            const { label, Icon } = OPTIONS[scheme];
            const checked = scheme === selected;
            return (
              <button
                key={scheme}
                ref={(node) => {
                  items.current[scheme] = node;
                }}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                tabIndex={-1}
                onClick={() => choose(scheme)}
                className={cn(
                  "flex h-11 w-full items-center gap-2 rounded-tag px-3 text-ui font-medium",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset",
                  styles.item,
                  checked && styles.checked,
                )}
              >
                <Icon size={16} aria-hidden />
                <span className="flex-1 text-left">{label}</span>
                {checked && <Check size={16} aria-hidden />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
