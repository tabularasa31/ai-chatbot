"use client";

import { useEffect, type ReactNode } from "react";
import { buttonClasses } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { resolveScheme } from "@/lib/scheme";
import { SiteFooter } from "./SiteFooter";
import { SiteHeader } from "./SiteHeader";

export function MarketingShell({ children }: { children: ReactNode }) {
  useEffect(() => {
    resolveScheme();
  }, []);

  return (
    <div data-scheme-surface className="relative flex min-h-dvh flex-col bg-canvas text-fg">
      <a
        href="#content"
        className={cn(
          buttonClasses({ variant: "primary" }),
          "absolute left-4 top-2 z-50 -translate-y-20 focus-visible:translate-y-0",
        )}
      >
        Skip to content
      </a>
      <SiteHeader />
      <main id="content" tabIndex={-1} className="flex-1 focus:outline-none">
        {children}
      </main>
      <SiteFooter />
    </div>
  );
}
