import { motion } from "motion/react";
import { useScrollAnimation } from "../hooks/useScrollAnimation";
import { MessageCircle, Send } from "lucide-react";

export function DemoBlock() {
  const [ref, isInView] = useScrollAnimation();

  return (
    <section className="max-w-7xl mx-auto px-6 py-20">
      <motion.div
        ref={ref}
        initial={{ opacity: 0, y: 20 }}
        animate={
          isInView
            ? { opacity: 1, y: 0 }
            : { opacity: 0, y: 20 }
        }
        transition={{ duration: 0.6 }}
        className="text-center mb-12"
      >
        <h2 className="text-[#FAF5FF] text-4xl md:text-5xl mb-4">
          See Chat9 in action
        </h2>
        <p className="text-[#FAF5FF]/60 text-xl">
          Ask it anything about our docs
        </p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={
          isInView
            ? { opacity: 1, y: 0 }
            : { opacity: 0, y: 20 }
        }
        transition={{ duration: 0.6, delay: 0.2 }}
        className="max-w-4xl mx-auto"
      >
        <div className="bg-[#12121A] backdrop-blur-sm border border-[#1E1E2E] rounded-2xl overflow-hidden h-[600px] flex flex-col">
          {/* Chat Widget Header */}
          <div className="bg-[#0A0A0F]/50 border-b border-[#1E1E2E] px-6 py-4 flex items-center gap-3">
            <div className="w-10 h-10 bg-[#E879F9] rounded-full flex items-center justify-center">
              <MessageCircle
                size={20}
                className="text-[#0A0A0F]"
              />
            </div>
            <div>
              <div className="text-[#FAF5FF] font-medium">
                Chat9 Assistant
              </div>
              <div className="text-[#FAF5FF]/60 text-sm">
                Online
              </div>
            </div>
          </div>

          {/* Chat Messages Area */}
          <div className="flex-1 p-6 overflow-y-auto">
            <div className="space-y-4">
              {/* Bot Message */}
              <div className="flex gap-3">
                <div className="w-8 h-8 bg-[#E879F9] rounded-full flex items-center justify-center flex-shrink-0">
                  <MessageCircle
                    size={16}
                    className="text-[#0A0A0F]"
                  />
                </div>
                <div className="bg-[#2D2D44] rounded-lg rounded-tl-none px-4 py-3 max-w-[80%]">
                  <p className="text-[#FAF5FF]">
                    Hi! I'm your Chat9 support assistant. I can
                    help you with questions about our
                    documentation, pricing, and features. What
                    would you like to know?
                  </p>
                </div>
              </div>

              {/* User Message */}
              <div className="flex gap-3 justify-end">
                <div className="bg-[#38BDF8] rounded-lg rounded-tr-none px-4 py-3 max-w-[80%]">
                  <p className="text-[#0A0A0F]">
                    How does the 24/7 support work?
                  </p>
                </div>
              </div>

              {/* Bot Response */}
              <div className="flex gap-3">
                <div className="w-8 h-8 bg-[#E879F9] rounded-full flex items-center justify-center flex-shrink-0">
                  <MessageCircle
                    size={16}
                    className="text-[#0A0A0F]"
                  />
                </div>
                <div className="bg-[#2D2D44] rounded-lg rounded-tl-none px-4 py-3 max-w-[80%]">
                  <p className="text-[#FAF5FF]">
                    Chat9 is always online and ready to help
                    your customers. It learns from your
                    documentation and can answer questions.
                    instantly, any time of day or night. You'll
                    receive daily reports showing all
                    interactions.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Chat Input */}
          <div className="border-t border-[#1E1E2E] p-4">
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Type your message..."
                className="flex-1 bg-[#0A0A0F]/50 border border-[#1E1E2E] rounded-lg px-4 py-3 text-[#FAF5FF] placeholder-[#FAF5FF]/40 focus:outline-none focus:ring-2 focus:ring-[#E879F9]"
              />
              <button className="bg-[#E879F9] text-[#0A0A0F] px-6 py-3 rounded-lg hover:bg-[#f099fb] hover:scale-105 transition-all">
                <Send size={20} />
              </button>
            </div>
          </div>
        </div>
      </motion.div>
    </section>
  );
}