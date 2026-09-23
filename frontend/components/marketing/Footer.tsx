import Link from "next/link";
import { Logo } from "@/components/Logo";

export function Footer() {
  return (
    <footer className="bg-nd-base-alt border-t border-nd-surface">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          {/* Logo */}
          <Link href="/" aria-label="Chat9 home">
            <Logo size={22} textClassName="text-xl" />
          </Link>

          {/* Links */}
          <div className="flex items-center gap-8">
            <Link
              href="/blog"
              className="text-nd-text/80 hover:text-nd-info transition-colors"
            >
              Blog
            </Link>
            <Link
              href="/docs"
              className="text-nd-text/80 hover:text-nd-info transition-colors"
            >
              Docs
            </Link>
          </div>

          {/* Copyright */}
          <div className="text-nd-text/40 text-sm">© 2026 Chat9</div>
        </div>
      </div>
    </footer>
  );
}
