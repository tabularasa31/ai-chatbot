import { useState } from 'react';
import { Menu, X } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';

export function Navigation() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const scrollToSection = (id: string) => {
    const element = document.getElementById(id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth' });
      setMobileMenuOpen(false);
    }
  };

  return (
    <nav className="sticky top-0 z-50 bg-[#0A0A0F]/90 backdrop-blur-md border-b border-[#1E1E2E]">
      <div className="max-w-7xl mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          {/* Logo */}
          <div className="text-[#FAF5FF] text-xl font-semibold">Chat9</div>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center gap-8">
            <button
              onClick={() => scrollToSection('features')}
              className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
            >
              Docs
            </button>
            <a
              href="https://github.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
            >
              GitHub
            </a>
          </div>

          {/* CTA Button - Desktop */}
          <button className="hidden md:block bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all">
            Try for free
          </button>

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
                <button
                  onClick={() => scrollToSection('features')}
                  className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors text-left"
                >
                  Docs
                </button>
                <a
                  href="https://github.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#FAF5FF]/80 hover:text-[#FAF5FF] transition-colors"
                >
                  GitHub
                </a>
                <button className="bg-[#E879F9] text-[#0A0A0F] px-6 py-2 rounded-lg hover:bg-[#f099fb] transition-colors">
                  Try for free
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </nav>
  );
}