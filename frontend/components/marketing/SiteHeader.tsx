"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { Logo } from "@/components/Logo";
import { FOCUS_RING } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { CONTAINER, navLinkClasses } from "./classes";
import { useSignedIn } from "./useSignedIn";

const MENU_ID = "site-menu";

function isCurrent(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname();
  const signedIn = useSignedIn();
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      menuButton.current?.focus();
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  const links = [
    { href: "/docs", label: "Docs" },
    { href: "/blog", label: "Blog" },
    signedIn ? { href: "/dashboard", label: "Dashboard" } : { href: "/login", label: "Sign in" },
  ];

  return (
    <header className="bg-canvas">
      <div className="border-b border-line">
        <div className={cn(CONTAINER, "flex h-16 items-center justify-between")}>
          <Link
            href="/"
            aria-label="Chat9 home"
            className={cn("inline-flex min-h-11 min-w-11 items-center rounded-tag", FOCUS_RING.canvas)}
          >
            <Logo size={22} textClassName="text-xl text-fg" />
          </Link>
          <nav aria-label="Main" className="hidden items-center md:flex">
            {links.map(({ href, label }) => {
              const current = isCurrent(pathname, href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={current ? "page" : undefined}
                  className={cn(navLinkClasses, "border-b-2", current ? "border-brand text-fg" : "border-transparent")}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <button
            ref={menuButton}
            type="button"
            aria-expanded={open}
            aria-controls={MENU_ID}
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((value) => !value)}
            className={cn(
              "flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-alt hover:text-fg md:hidden",
              "transition-colors duration-[120ms] motion-reduce:transition-none",
              FOCUS_RING.canvas,
            )}
          >
            {open ? <X size={20} aria-hidden /> : <Menu size={20} aria-hidden />}
          </button>
        </div>
      </div>
      {open && (
        <nav id={MENU_ID} aria-label="Menu" className="border-b border-line md:hidden">
          <ul>
            {links.map(({ href, label }) => {
              const current = isCurrent(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={current ? "page" : undefined}
                    onClick={() => setOpen(false)}
                    className={cn(
                      "flex h-11 w-full items-center px-4 text-ui font-medium hover:bg-surface-alt",
                      current ? "text-fg" : "text-fg-muted",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
                    )}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}
    </header>
  );
}
