"use client";

import { useEffect, useState } from "react";
import { api, type TenantMember } from "@/lib/api";
import { useClientMe, useMembers } from "@/hooks/useApi";

/** Monthly price of one seat, in US dollars. Nothing is charged during the beta. */
const SEAT_PRICE_USD = 10;

function money(amount: number): string {
  return `$${amount}`;
}

function seatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
}

export default function SeatsPage() {
  const { data: client } = useClientMe();
  const { data, error: loadError, isLoading, mutate } = useMembers();

  const [selfId, setSelfId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // /tenants/me carries the role, not the user id, and "your own seat" needs
  // the id to find your row in the list.
  useEffect(() => {
    api.auth
      .getMe()
      .then((user) => setSelfId(user.id))
      .catch(() => {});
  }, []);

  const isOwner = client?.role === "owner";
  const members: TenantMember[] = data?.items ?? [];
  const seats = data?.seats ?? 0;
  const me = members.find((m) => m.id === selfId) ?? null;
  const iAmSeated = Boolean(me?.seat_granted_at);

  async function setOwnSeat(take: boolean) {
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const updated = take
        ? await api.members.takeOwnSeat()
        : await api.members.giveUpOwnSeat();
      await mutate();
      setNotice(
        updated.seat_granted_at
          ? "You have a seat. Nothing was charged."
          : "Your seat is back. You still run the workspace."
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : take
            ? "Failed to take a seat"
            : "Failed to give up your seat"
      );
    } finally {
      setSaving(false);
    }
  }

  if (client && !isOwner) {
    return (
      <div className="max-w-3xl">
        <h1 className="text-2xl font-semibold text-slate-800">Seats</h1>
        <p className="text-slate-500 text-sm mt-2">
          Only an owner can see and change the workspace&apos;s seats.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Seats</h1>
        <p className="text-slate-500 text-sm mt-1">
          A seat is what lets someone answer. Everyone in the workspace can sign
          in and read; a seat is what turns reading into working — the operator
          console, replies that land in the chat thread where the visitor sees
          them, and answers that become source material for documentation
          drafts.
        </p>
        <p className="text-slate-500 text-sm mt-2">
          Without a seat, a reply takes the ordinary email path: out of your own
          mailbox, into the visitor&apos;s, never through Chat9 and never into
          the conversation.
        </p>
      </div>

      {(error || loadError) && (
        <div className="rounded-lg bg-red-50 text-red-600 text-sm px-3 py-2 border border-red-100">
          {error ||
            (loadError instanceof Error
              ? loadError.message
              : "Failed to load your seats")}
        </div>
      )}

      {notice && (
        <div className="rounded-lg bg-emerald-50 text-emerald-700 text-sm px-3 py-2 border border-emerald-100">
          {notice}
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
        {isLoading && !data ? (
          <div className="text-slate-400 text-sm">Loading…</div>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-2xl font-semibold text-slate-800">
                {seats} {seats === 1 ? "seat" : "seats"}
              </span>
              <span className="text-sm text-slate-500">
                × {money(SEAT_PRICE_USD)} per seat per month ={" "}
                {money(seats * SEAT_PRICE_USD)} a month
              </span>
            </div>
            <p className="text-sm text-slate-600">
              Nothing is charged. Seats are free while Chat9 is in beta, there is
              no payment method on file, and no invoice is raised — the figure
              above is what these seats will cost when billing starts.
            </p>
          </>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Your own seat</h2>
          <p className="text-slate-500 text-sm mt-1">
            Being the owner is not a seat. You run the workspace — keys, team,
            knowledge base, settings — without one and at no cost. Take a seat
            only if you also want to answer conversations yourself.
          </p>
        </div>

        {me === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : iAmSeated ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-slate-600">
              You have a seat{me.seat_granted_at ? ` since ${seatDate(me.seat_granted_at)}` : ""}.
            </span>
            <button
              type="button"
              onClick={() => setOwnSeat(false)}
              disabled={saving}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-40 transition-colors"
            >
              {saving ? "Working…" : "Give up my seat"}
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-slate-600">
              You have no seat, so your replies take the ordinary email path.
            </span>
            <button
              type="button"
              onClick={() => setOwnSeat(true)}
              disabled={saving}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-violet-600 hover:bg-violet-700 text-white disabled:opacity-40 transition-colors"
            >
              {saving ? "Working…" : `Take a seat — ${money(SEAT_PRICE_USD)} a month`}
            </button>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h2 className="text-base font-semibold text-slate-800">Who holds a seat</h2>
        <p className="text-slate-500 text-sm mt-1 mb-4">
          Inviting a colleague gives them a seat, so they can answer from the
          moment they join. Removing them gives it back. Both are on the{" "}
          <a href="/settings/members" className="text-violet-600 hover:underline">
            Team
          </a>{" "}
          screen.
        </p>

        {isLoading && !data ? (
          <div className="text-slate-400 text-sm">Loading…</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {members.map((member) => (
              <div
                key={member.id}
                className="py-3 flex items-center gap-3 flex-wrap"
              >
                <span className="text-sm text-slate-800">{member.email}</span>
                {member.status === "pending" && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded bg-amber-100 text-amber-700">
                    invite pending
                  </span>
                )}
                <span className="ml-auto text-xs text-slate-500">
                  {member.seat_granted_at
                    ? `Seat since ${seatDate(member.seat_granted_at)}`
                    : "No seat"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-slate-500">
        Some of what a seat gets you is still being built: today the operator
        actions are an API, and the console, replies by email and the
        documentation loop are on the way.
      </p>
    </div>
  );
}
