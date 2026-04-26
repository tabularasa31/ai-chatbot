import { motion } from 'framer-motion';
import Link from 'next/link';
import { Send } from 'lucide-react';

export function Hero() {
  return (
    <section className="max-w-7xl mx-auto px-6 py-20 md:py-32">
      <div className="grid md:grid-cols-2 gap-12 md:gap-16 items-center">
        {/* Left Column - Text Content */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
        >
          <h1 className="text-5xl md:text-6xl lg:text-7xl text-nd-text mb-6">
            Meet your new support mate.
          </h1>
          <p className="text-xl text-nd-text/80 mb-8">
            Works 24/7. Sends you a daily report. Gets better every week.
          </p>
          <div className="flex flex-col sm:flex-row gap-4">
            <Link
              href="/signup"
              className="bg-nd-accent text-nd-base px-8 py-3 rounded-lg hover:bg-nd-accent-hover hover:scale-105 transition-all inline-block text-center"
            >
              Try for free
            </Link>
            <a
              href="#demo"
              className="border border-nd-info text-nd-text px-8 py-3 rounded-lg hover:bg-nd-info/10 hover:scale-105 transition-all inline-block text-center"
            >
              See demo
            </a>
          </div>
        </motion.div>

        {/* Right Column - Chat UI Mockup */}
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="relative"
        >
          {/* Glow Effects */}
          <div className="absolute inset-0 bg-nd-accent/20 rounded-3xl blur-3xl"></div>
          <div className="absolute inset-0 bg-nd-info/20 rounded-3xl blur-3xl"></div>
          
          {/* Chat Card */}
          <div className="relative bg-nd-base-alt rounded-2xl p-6 shadow-2xl border border-nd-surface">
            {/* Chat Header */}
            <div className="flex items-center gap-3 mb-6 pb-4 border-b border-nd-surface">
              <div className="w-10 h-10 bg-nd-accent rounded-full flex items-center justify-center text-nd-base font-semibold">
                AI
              </div>
              <div>
                <div className="text-nd-text font-medium">Chat9 Assistant</div>
                <div className="text-nd-text/60 text-sm">Online</div>
              </div>
            </div>

            {/* Messages */}
            <div className="space-y-4 mb-6">
              {/* Incoming Message */}
              <div className="flex gap-2">
                <div className="bg-[#2D2D44] rounded-2xl rounded-tl-sm px-4 py-3 max-w-[85%]">
                  <p className="text-nd-text text-sm">
                    Hi! How can I help you today?
                  </p>
                </div>
              </div>

              {/* Outgoing Message */}
              <div className="flex justify-end">
                <div className="bg-nd-info rounded-2xl rounded-tr-sm px-4 py-3 max-w-[85%]">
                  <p className="text-nd-base text-sm">
                    What are your pricing plans?
                  </p>
                </div>
              </div>

              {/* Incoming Message */}
              <div className="flex gap-2">
                <div className="bg-[#2D2D44] rounded-2xl rounded-tl-sm px-4 py-3 max-w-[85%]">
                  <p className="text-nd-text text-sm">
                    We have flexible plans starting at $29/month. Would you like more details?
                  </p>
                </div>
              </div>
            </div>

            {/* Input Field */}
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Type a message..."
                aria-label="Type a message"
                className="flex-1 bg-nd-base/50 border border-nd-surface rounded-lg px-4 py-2.5 text-nd-text text-sm placeholder-nd-text/40 focus:outline-none focus:ring-2 focus:ring-nd-accent"
              />
              <button className="bg-nd-accent p-2.5 rounded-lg hover:bg-nd-accent-hover transition-colors">
                <Send size={18} className="text-nd-base" />
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}