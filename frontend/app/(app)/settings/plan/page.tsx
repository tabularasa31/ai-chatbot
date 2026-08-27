"use client";

import { useState } from "react";
import { api, type TenantPlan } from "@/lib/api";
import { usePlan } from "@/hooks/useApi";

type Tier = {
  value: TenantPlan;
  name: string;
  price: string;
  summary: string;
  points: string[];
  footnote?: string;
};

const TIERS: Tier[] = [
  {
    value: "free",
    name: "Free",
    price: "Free",
    summary: "How escalations work today, and how they keep working if you stay here.",
    points: [
      "An escalation emails your support inbox.",
      "When you reply, your answer goes straight to the visitor's own email address — it never passes through Chat9.",
      "The chat thread never learns the question was answered, so you close the ticket by hand.",
    ],
  },
  {
    value: "pro",
    name: "Pro",
    price: "Free during the beta",
    summary: "The operator handoff: your reply comes back into the conversation.",
    points: [
      "An escalation emails your support inbox, the same as today.",
      "When you reply, your answer comes back through Chat9 into the chat thread — the visitor sees it in the widget, and it still reaches their email.",
      "An operator console in the dashboard, for answering without leaving Chat9.",
      "Telegram and Slack as alternative places to answer from.",
      "Answers you write become source material for documentation drafts in Gap Analyzer.",
    ],
    footnote: "Everything in this list is still being built.",
  },
];

export default function PlanSettingsPage() {
  const { data, error: loadError, isLoading, mutate } = usePlan();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const current = data?.plan ?? null;

  async function switchTo(plan: TenantPlan) {
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const response = await api.plan.update(plan);
      await mutate(response, false);
      setNotice(
        plan === "pro"
          ? "You're on Pro. Nothing was charged."
          : "You're back on Free."
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change your plan");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Plan</h1>
        <p className="text-slate-500 text-sm mt-1">
          Two tiers, and a switch you can move in either direction. Nothing on
          this page takes a payment: while Chat9 is in beta the paid tier costs
          nothing, and there is no payment method on file.
        </p>
      </div>

      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
        <p className="text-sm text-amber-900">
          <span className="font-medium">Not switched on yet.</span> The handoff
          features listed under Pro are still in development. Choosing Pro
          records your choice; it does not change how your bot or your
          escalations behave today.
        </p>
      </div>

      {(error || loadError) && (
        <div className="rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100">
          {error ||
            (loadError instanceof Error ? loadError.message : "Failed to load your plan")}
        </div>
      )}

      {notice && (
        <div className="rounded-lg bg-emerald-50 text-emerald-700 text-sm px-3 py-2 border border-emerald-100">
          {notice}
        </div>
      )}

      {isLoading && !data ? (
        <div className="text-slate-400 text-sm">Loading…</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {TIERS.map((tier) => {
            const isCurrent = current === tier.value;
            return (
              <div
                key={tier.value}
                className={`bg-white rounded-xl border p-6 flex flex-col gap-4 ${
                  isCurrent ? "border-violet-300 ring-1 ring-violet-100" : "border-slate-200"
                }`}
              >
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-semibold text-slate-800">{tier.name}</h2>
                    {isCurrent && (
                      <span className="px-2 py-0.5 text-xs font-medium rounded bg-violet-100 text-violet-700">
                        Current plan
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-slate-700 mt-1">{tier.price}</p>
                  <p className="text-sm text-slate-500 mt-1">{tier.summary}</p>
                </div>

                <ul className="space-y-2 text-sm text-slate-600 flex-1">
                  {tier.points.map((point) => (
                    <li key={point} className="flex gap-2">
                      <span aria-hidden="true" className="text-slate-300 mt-[2px]">
                        •
                      </span>
                      <span>{point}</span>
                    </li>
                  ))}
                </ul>

                {tier.footnote && (
                  <p className="text-xs text-slate-500">{tier.footnote}</p>
                )}

                <button
                  type="button"
                  onClick={() => switchTo(tier.value)}
                  disabled={saving || isCurrent || current === null}
                  className={`px-4 py-2 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors ${
                    tier.value === "pro"
                      ? "bg-violet-600 hover:bg-violet-700 text-white"
                      : "bg-slate-100 hover:bg-slate-200 text-slate-700"
                  }`}
                >
                  {isCurrent
                    ? "Current plan"
                    : saving
                      ? "Switching…"
                      : tier.value === "pro"
                        ? "Switch to Pro"
                        : "Switch back to Free"}
                </button>

                {tier.value === "pro" && !isCurrent && (
                  <p className="text-xs text-slate-500 -mt-2">
                    No card, no charge. You can switch back at any time.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs text-slate-500">
        Only the workspace owner can change the plan.
      </p>
    </div>
  );
}
