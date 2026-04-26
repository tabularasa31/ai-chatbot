"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Menu, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { hasSession } from '@/lib/api';

export function Navigation() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isAuthed, setIsAuthed] = useState(false);

  useEffect(() => {
    setIsAuthed(hasSession());
  }, []);

  return (
    <nav className="sticky top-0 z-50 bg-[#0A0A0F]/90 backdrop-blur-md border-b border-[#1E1E2E]">
      <div className="max-w-7xl mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          {/* Logo */}
          <Link href="/" className="text-[#FAF5FF] text-xl font-semibold">
            Chat9
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center gap-8">
            <Link
              href="/blog"
              className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
            >
              Blog
            </Link>
            <Link
              href="/docs"
              className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
            >
              Docs
            </Link>
            <a
              href="https://github.com/tabularasa31/chat9-sdks"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
            >
              GitHub
            </a>
          </div>

          {/* Sign in + CTA - Desktop */}
          <div className="hidden md:flex items-center gap-3">
            {isAuthed ? (
              <Link
                href="/dashboard"
                className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all"
              >
                Dashboard
              </Link>
            ) : (
              <>
                <Link
                  href="/login"
                  className="border border-[#67E8F9] text-[#FAF5FF] px-6 py-2 rounded-lg hover:bg-[#67E8F9]/10 hover:scale-105 transition-all"
                >
                  Sign in
                </Link>
                <Link
                  href="/signup"
                  className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all"
                >
                  Try for free
                </Link>
              </>
            )}
          </div>

          {/* Hamburger - Mobile */}
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="md:hidden text-[#FAF5FF]"
            aria-label="Toggle menu"
          >
            {mobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>

        {/* Mobile Menu */}
        <AnimatePresence>
          {mobileMenuOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="md:hidden overflow-hidden"
            >
              <div className="flex flex-col gap-4 py-6">
                <Link
                  href="/blog"
                  className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors text-left"
                >
                  Blog
                </Link>
                <Link
                  href="/docs"
                  className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors text-left"
                >
                  Docs
                </Link>
                <a
                  href="https://github.com/tabularasa31/chat9-sdks"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
                >
                  GitHub
                </a>
                {isAuthed ? (
                  <Link
                    href="/dashboard"
                    className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] transition-colors inline-block text-center"
                  >
                    Dashboard
                  </Link>
                ) : (
                  <>
                    <Link
                      href="/login"
                      className="border border-[#67E8F9] text-[#FAF5FF] px-6 py-2 rounded-lg hover:bg-[#67E8F9]/10 transition-all text-center"
                    >
                      Sign in
                    </Link>
                    <Link
                      href="/signup"
                      className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] transition-colors inline-block text-center"
                    >
                      Try for free
                    </Link>
                  </>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </nav>
  );
}
