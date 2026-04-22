"use client";

import { useEffect } from "react";
import { motion } from "framer-motion";

const LANDING_DEMO_BOT_ID =
  process.env.NEXT_PUBLIC_LANDING_DEMO_BOT_ID?.trim() || "anXhrQQoNkoWCF2ty2-Tg";

const API_URL = (process.env.NEXT_PUBLIC_API_URL?.trim() || "").replace(/\/$/, "");

const TARGET_ID = "chat9-landing-demo";

function DemoWidget() {
  useEffect(() => {
    // Widget iframe is served by Next.js at /widget on this origin,
    // not by the API host that serves embed.js.
    const w = window as typeof window & {
      Chat9Config?: { widgetUrl?: string };
    };
    const hadPriorConfig = Object.prototype.hasOwnProperty.call(w, "Chat9Config");
    const priorConfig = w.Chat9Config;
    w.Chat9Config = { ...(priorConfig ?? {}), widgetUrl: window.location.origin };

    const script = document.createElement("script");
    script.src = `${API_URL}/embed.js`;
    script.async = true;
    script.setAttribute("data-bot-id", LANDING_DEMO_BOT_ID);
    script.setAttribute("data-mode", "inline");
    script.setAttribute("data-target", TARGET_ID);
    document.body.appendChild(script);

    return () => {
      script.remove();
      const target = document.getElementById(TARGET_ID);
      if (target) target.innerHTML = "";
      if (hadPriorConfig) {
        w.Chat9Config = priorConfig;
      } else {
        delete w.Chat9Config;
      }
    };
  }, []);

  return <div id={TARGET_ID} className="w-full h-[600px]" />;
}

export function DemoBlock() {
  const ready = Boolean(LANDING_DEMO_BOT_ID && API_URL);

  return (
    <section id="demo" className="max-w-7xl mx-auto px-6 py-20">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.1 }}
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
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.1 }}
        transition={{ duration: 0.6, delay: 0.2 }}
        className="max-w-4xl mx-auto"
      >
        <div className="bg-[#12121A] border border-[#1E1E2E] rounded-2xl overflow-hidden">
          {ready ? (
            <DemoWidget />
          ) : (
            <div className="h-[600px] flex items-center justify-center px-6">
              <p className="text-[#FAF5FF]/40 text-sm text-center leading-relaxed max-w-sm">
                Live demo unavailable — set{" "}
                <code className="text-[#FAF5FF]/60 text-xs">
                  NEXT_PUBLIC_API_URL
                </code>{" "}
                and{" "}
                <code className="text-[#FAF5FF]/60 text-xs">
                  NEXT_PUBLIC_LANDING_DEMO_BOT_ID
                </code>
                .
              </p>
            </div>
          )}
        </div>
      </motion.div>
    </section>
  );
}
