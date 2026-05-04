"use client";

import { useEffect } from "react";
import { motion } from "framer-motion";

const LANDING_DEMO_BOT_ID = process.env.NEXT_PUBLIC_LANDING_DEMO_BOT_ID?.trim();

const WIDGET_LOADER_URL =
  process.env.NEXT_PUBLIC_WIDGET_LOADER_URL || "https://widget.getchat9.live/widget.js";

const TARGET_ID = "chat9-landing-demo";

function DemoWidget() {
  useEffect(() => {
    // Loader's hardcoded apiBase points at production getchat9.live, but the
    // landing page may run on a preview/staging host — point the embedded
    // widget back at *this* origin so /widget/* requests land on the dashboard
    // serving the page.
    const script = document.createElement("script");
    script.src = WIDGET_LOADER_URL;
    script.async = true;
    script.setAttribute("data-bot-id", LANDING_DEMO_BOT_ID!);
    script.setAttribute("data-mode", "inline");
    script.setAttribute("data-target", TARGET_ID);
    script.setAttribute("data-api-base", window.location.origin);
    document.body.appendChild(script);

    return () => {
      script.remove();
      const target = document.getElementById(TARGET_ID);
      if (target) target.innerHTML = "";
    };
  }, []);

  return <div id={TARGET_ID} className="w-full h-[600px]" />;
}

export function DemoBlock() {
  const ready = Boolean(LANDING_DEMO_BOT_ID);

  return (
    <section id="demo" className="max-w-7xl mx-auto px-6 py-20">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.1 }}
        transition={{ duration: 0.6 }}
        className="text-center mb-12"
      >
        <h2 className="text-nd-text text-4xl md:text-5xl mb-4">
          See Chat9 in action
        </h2>
        <p className="text-nd-text/60 text-xl">
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
        <div className="bg-nd-base-alt border border-nd-surface rounded-2xl overflow-hidden">
          {ready ? (
            <DemoWidget />
          ) : (
            <div className="h-[600px] flex items-center justify-center px-6">
              <p className="text-nd-text/40 text-sm text-center leading-relaxed max-w-sm">
                Live demo unavailable — set{" "}
                <code className="text-nd-text/60 text-xs">
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
