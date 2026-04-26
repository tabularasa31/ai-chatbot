import Link from "next/link";

export function Footer() {
  return (
    <footer className="bg-nd-base-alt border-t border-nd-surface">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          {/* Logo */}
          <Link href="/" className="text-nd-text text-xl font-semibold">
            Chat9
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
            <a
              href="https://github.com/tabularasa31/chat9-sdks"
              target="_blank"
              rel="noopener noreferrer"
              className="text-nd-text/80 hover:text-nd-info transition-colors"
            >
              GitHub
            </a>
          </div>

          {/* Copyright */}
          <div className="text-nd-text/40 text-sm">© 2026 Chat9</div>
        </div>
      </div>
    </footer>
  );
}
