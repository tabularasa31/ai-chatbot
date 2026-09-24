import type { ReactNode } from "react";

import { cn } from "./utils";

type AlertTone = "error" | "success";

/**
 * Each key reproduces one of the pre-existing banner class strings byte-for-byte
 * (per call site) so replacing inline markup with <Alert> changes no visual output.
 */
type AlertVariant =
  | "soft"
  | "compact"
  | "card"
  | "cardLg"
  | "plain"
  | "rose"
  | "widget"
  | "preLine"
  | "dot";

const ALERT_CLASSES: Record<`${AlertTone}:${AlertVariant}`, string> = {
  "error:soft":
    "rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100",
  "error:compact": "bg-red-50 text-red-700 px-3 py-2 rounded-lg text-sm mb-3",
  "error:card":
    "rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700",
  "error:cardLg":
    "rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700",
  "error:plain": "bg-red-50 text-red-700 px-4 py-3 rounded-lg",
  "error:rose":
    "rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700",
  "error:widget":
    "rounded-lg border border-red-200 bg-red-50 text-red-800 px-4 py-3 text-sm",
  "error:dot": "",
  "error:preLine":
    "whitespace-pre-line rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600",
  "success:preLine": "",
  "success:soft":
    "text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg",
  "success:dot":
    "flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg",
  "success:cardLg":
    "rounded-lg border border-green-100 bg-green-50 px-4 py-3 text-sm text-green-700",
  "success:rose":
    "rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700",
  "success:card": "",
  "success:compact": "",
  "success:plain": "",
  "success:widget": "",
};

export function Alert({
  tone,
  variant = "soft",
  className,
  children,
}: {
  tone: AlertTone;
  variant?: AlertVariant;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(ALERT_CLASSES[`${tone}:${variant}`], className)}
    >
      {children}
    </div>
  );
}
