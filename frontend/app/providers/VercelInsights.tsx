"use client";

import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { redactInsightsEvent } from "@/lib/analytics-privacy";

export function VercelInsights() {
  return (
    <>
      <Analytics beforeSend={redactInsightsEvent} />
      <SpeedInsights beforeSend={redactInsightsEvent} />
    </>
  );
}
