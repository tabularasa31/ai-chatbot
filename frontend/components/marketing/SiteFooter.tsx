import Link from "next/link";
import { Logo } from "@/components/Logo";
import { FOCUS_RING } from "@/components/ui/button";
import { ThemeSwitch } from "@/components/ui/theme-switch";
import { cn } from "@/components/ui/utils";
import { CONTAINER, navLinkClasses } from "./classes";

const LINKS = [
  { href: "/docs", label: "Docs" },
  { href: "/blog", label: "Blog" },
  { href: "/docs/changelog", label: "Changelog" },
  { href: "/docs/pricing-and-limits", label: "Pricing and limits" },
  { href: "mailto:support@getchat9.live", label: "Contact support" },
];

export function SiteFooter() {
  return (
    <footer className="border-t border-line bg-canvas py-12">
      <div className={CONTAINER}>
        <div className="flex flex-col items-start gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-6">
          <Link
            href="/"
            aria-label="Chat9 home"
            className={cn("inline-flex min-h-11 min-w-11 items-center rounded-tag", FOCUS_RING.canvas)}
          >
            <Logo size={22} textClassName="text-xl text-fg" />
          </Link>
          <nav aria-label="Footer" className="-mx-3 flex flex-wrap items-center">
            {LINKS.map(({ href, label }) => (
              <Link key={href} href={href} className={navLinkClasses}>
                {label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="mt-6 flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-caption text-fg-muted">© 2026 Chat9</p>
          <div className="flex items-center gap-3">
            <span className="text-caption text-fg-muted">Theme</span>
            <ThemeSwitch />
          </div>
        </div>
      </div>
    </footer>
  );
}
