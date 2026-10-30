"use client";

import Link from "next/link";
import { useState } from "react";
import { api, type TenantMember } from "@/lib/api";
import { useAuthUser, useMembers } from "@/hooks/useApi";
import { formatDate, parseApiDate, pluralize } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LoadError } from "@/components/ui/load-error";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { SettingsShell } from "../_components/SettingsShell";
import { linkClasses, SaveRow, SettingsSection, useSavedMessage } from "../_components/SettingsSection";

/** Monthly price of one seat, in US dollars. Nothing is charged during the beta. */
const SEAT_PRICE_USD = 10;

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function seatDate(value: string): string {
  const parsed = parseApiDate(value);
  return Number.isNaN(parsed.getTime()) ? "" : formatDate(value);
}

function seatLabel(member: TenantMember): string {
  if (member.seat_granted_at) return `Seat since ${seatDate(member.seat_granted_at)}`;
  return member.status === "pending" ? "Seat on acceptance" : "No seat";
}

function SeatSummary({ seats }: { seats: number }) {
  return (
    <div>
      <p className="text-h2 text-fg">
        {seats.toLocaleString()} {pluralize(seats, "seat", "seats")}
      </p>
      <p className="text-ui text-fg-muted">
        {`At ${USD.format(SEAT_PRICE_USD)} per seat per month, that is ${USD.format(seats * SEAT_PRICE_USD)} a month. Free during the beta: no payment method on file, nothing is charged.`}
      </p>
      <p className="mt-2 text-caption text-fg-muted">
        A seat lets someone take and answer conversations in the Inbox. Everyone else can read.
      </p>
    </div>
  );
}

function OwnSeatSection({ members, onChange }: { members: TenantMember[]; onChange: () => Promise<unknown> }) {
  const { data: authUser, error, mutate } = useAuthUser();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useSavedMessage();

  if (!authUser) {
    return (
      <SettingsSection title="Your seat">
        {error ? (
          <LoadError message="Couldn't load your seat." onRetry={() => mutate()} />
        ) : (
          <SkeletonRegion>
            <Skeleton className="h-11 w-64 max-w-full" />
          </SkeletonRegion>
        )}
      </SettingsSection>
    );
  }

  const seatedSince = members.find((member) => member.id === authUser.id)?.seat_granted_at ?? null;

  async function run(action: () => Promise<unknown>, doneText: string, failureText: string) {
    setBusy(true);
    setFailure(null);
    setSaved(null);
    try {
      await action();
      await onChange();
      setSaved(doneText);
    } catch {
      setFailure(failureText);
    } finally {
      setBusy(false);
    }
  }

  const takeSeat = () => run(() => api.members.takeOwnSeat(), "You have a seat.", "Couldn't take a seat. Try again.");
  const giveUpSeat = () =>
    run(() => api.members.giveUpOwnSeat(), "Seat given up.", "Couldn't give up your seat. Try again.");

  return (
    <SettingsSection title="Your seat">
      {seatedSince ? (
        <p className="text-ui text-fg">You have a seat since {seatDate(seatedSince)}.</p>
      ) : (
        <>
          <p className="text-ui text-fg">You don&apos;t have a seat, so you can&apos;t take or answer conversations.</p>
          <p className="text-caption text-fg-muted">
            Being the owner isn&apos;t a seat. Take one only to answer conversations yourself.
          </p>
        </>
      )}
      {failure && <InlineNotice tone="danger">{failure}</InlineNotice>}
      <SaveRow saved={saved}>
        {seatedSince ? (
          <Button variant="secondary" busy={busy} onClick={() => void giveUpSeat()}>
            Give up my seat
          </Button>
        ) : (
          <Button variant="primary" busy={busy} onClick={() => void takeSeat()}>
            Take a seat
          </Button>
        )}
      </SaveRow>
    </SettingsSection>
  );
}

function SeatHoldersSection({ members }: { members: TenantMember[] }) {
  return (
    <SettingsSection title="Seat holders">
      <ul className="divide-y divide-line">
        {members.map((member) => (
          <li key={member.id} className="flex min-h-11 flex-wrap items-center gap-x-3 py-2">
            <div className="min-w-0 flex-1">
              <p className="break-anywhere text-ui text-fg">{member.email}</p>
              {member.status === "pending" && <p className="text-caption text-fg-muted">Invite pending</p>}
            </div>
            <span className="shrink-0 whitespace-nowrap text-caption text-fg-muted tabular-nums">{seatLabel(member)}</span>
          </li>
        ))}
      </ul>
      <p className="text-caption text-fg-muted">
        Invite and remove people on the{" "}
        <Link href="/settings/members" className={linkClasses}>
          Team
        </Link>{" "}
        tab.
      </p>
    </SettingsSection>
  );
}

function SeatsPanel() {
  const { data, error, mutate } = useMembers();

  if (!data) {
    return error ? (
      <LoadError message="Couldn't load seats." onRetry={() => mutate()} />
    ) : (
      <SkeletonRegion>
        <div className="space-y-3">
          <Skeleton className="h-7 w-32" />
          <Skeleton className="w-full max-w-md" />
        </div>
      </SkeletonRegion>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <SeatSummary seats={data.seats} />
      <OwnSeatSection members={data.items} onChange={() => mutate()} />
      <SeatHoldersSection members={data.items} />
    </div>
  );
}

export default function SeatsPage() {
  return (
    <SettingsShell active="seats">
      <SeatsPanel />
    </SettingsShell>
  );
}
