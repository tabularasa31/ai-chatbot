import Link from "next/link";

export function Footer() {
  return (
    <footer className="bg-[#12121A] border-t border-[#1E1E2E]">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          {/* Logo */}
          <Link href="/" className="text-[#FAF5FF] text-xl font-semibold">
            Chat9
          </Link>

          {/* Links */}
          <div className="flex items-center gap-8">
            <Link
              href="/blog"
              className="text-[#FAF5FF]/80 hover:text-[#38BDF8] transition-colors"
            >
              Blog
            </Link>
            <Link
              href="/docs"
              className="text-[#FAF5FF]/80 hover:text-[#38BDF8] transition-colors"
            >
              Docs
            </Link>
            <a
              href="https://github.com/tabularasa31/chat9-sdks"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[#FAF5FF]/80 hover:text-[#38BDF8] transition-colors"
            >
              GitHub
            </a>
          </div>

          {/* Copyright */}
          <div className="text-[#FAF5FF]/40 text-sm">© 2026 Chat9</div>
        </div>
      </div>
    </footer>
  );
}
