"use client";

import { useEffect, useState } from "react";
import { api, type TenantMember, type TenantRole, type TenantRoleValue } from "@/lib/api";
import { useClientMe, useMembers } from "@/hooks/useApi";

const ROLE_LABEL: Record<TenantRole, string> = {
  owner: "Owner",
  operator: "Operator",
};

function RoleBadge({ role }: { role: TenantRoleValue }) {
  const styles: Record<TenantRole, string> = {
    owner: "bg-violet-100 text-violet-700",
    operator: "bg-slate-100 text-slate-600",
  };
  // Falls back to the raw value: a role this build does not know still names
  // itself rather than rendering as an empty badge.
  const style = styles[role as TenantRole] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded ${style}`}>
      {ROLE_LABEL[role as TenantRole] ?? role}
    </span>
  );
}

export default function MembersPage() {
  const { data: client } = useClientMe();
  const { data, error: loadError, isLoading, mutate } = useMembers();
  const members = data?.items;

  const [email, setEmail] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [selfId, setSelfId] = useState<string | null>(null);

  // Who "you" are: /tenants/me carries the role, not the user id, and the
  // self-removal guard needs the id.
  useEffect(() => {
    api.auth
      .getMe()
      .then((user) => setSelfId(user.id))
      .catch(() => {});
  }, []);

  const isOwner = client?.role === "owner";

  async function invite() {
    const address = email.trim();
    if (!address) return;
    setInviting(true);
    setInviteError("");
    setNotice("");
    try {
      const result = await api.members.invite(address);
      setEmail("");
      setNotice(
        `Invite sent to ${result.member.email}. Their seat — and the $10 a ` +
          `month — starts when they accept. The link expires in 7 days.`
      );
      await mutate();
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Failed to send the invite");
    } finally {
      setInviting(false);
    }
  }

  async function remove(member: TenantMember) {
    if (
      !confirm(
        `Remove ${member.email}? This deletes their account. Their past replies ` +
          `stay in the transcripts, signed with their address.`
      )
    )
      return;
    setBusyId(member.id);
    setActionError("");
    try {
      await api.members.remove(member.id);
      await mutate();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to remove the member");
    } finally {
      setBusyId(null);
    }
  }

  if (client && !isOwner) {
    return (
      <div className="max-w-3xl">
        <h1 className="text-2xl font-semibold text-slate-800">Team</h1>
        <p className="text-slate-500 text-sm mt-2">
          Only an owner can manage team members.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Team</h1>
        <p className="text-slate-500 text-sm mt-1">
          Invite colleagues to work the inbox with you. Everyone you invite is
          an operator: they answer conversations and read the knowledge base,
          while settings, API keys and publishing stay with you as the owner.
          Roles do not change — a workspace has one owner, the person who
          created it. Removing someone deletes their account; their past
          replies stay in the transcripts.
        </p>
        <p className="text-slate-500 text-sm mt-2">
          A colleague gets a seat — which is what lets them answer — when they
          accept their invitation, at $10 per seat per month counted from the
          day they join rather than the day you invite them. Removing them gives
          the seat back. Nothing is charged while Chat9 is in beta; see{" "}
          <a href="/settings/seats" className="text-violet-600 hover:underline">
            Seats
          </a>
          .
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Invite someone</h2>
          <p className="text-slate-500 text-sm">
            They join as an operator: the inbox, the logs, and read access to
            the knowledge base.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && invite()}
            placeholder="colleague@company.com"
            aria-label="Colleague email"
            className="flex-1 min-w-[220px] px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-slate-400 placeholder:text-slate-400"
          />
          <button
            type="button"
            onClick={invite}
            disabled={inviting || !email.trim()}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
          >
            {inviting ? "Sending…" : "Send invite"}
          </button>
        </div>

        {inviteError && <p className="text-red-600 text-sm">{inviteError}</p>}
        {notice && (
          <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">
            {notice}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h2 className="text-base font-semibold text-slate-800 mb-4">Members</h2>

        {loadError && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded-lg text-sm mb-3">
            {loadError instanceof Error ? loadError.message : "Failed to load members"}
          </div>
        )}
        {actionError && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded-lg text-sm mb-3">
            {actionError}
          </div>
        )}

        {isLoading ? (
          <div className="text-slate-400 text-sm">Loading…</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {(members ?? []).map((member) => {
              const isSelf = member.id === selfId;
              // The owner is unremovable, and is normally also the viewer.
              // Keyed off the role rather than off `isSelf` alone because the
              // self id arrives from a second request: for that first moment
              // `isSelf` is false, and the button must not be live.
              const isOwnerRow = member.role === "owner";
              return (
                <div key={member.id} className="py-3 flex items-center gap-3 flex-wrap">
                  <span className="text-sm text-slate-800">{member.email}</span>
                  <RoleBadge role={member.role} />
                  {member.status === "pending" && (
                    <span className="px-2 py-0.5 text-xs font-medium rounded bg-amber-100 text-amber-700">
                      invite pending
                    </span>
                  )}
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => remove(member)}
                      disabled={busyId === member.id || isOwnerRow || isSelf}
                      title={
                        isOwnerRow
                          ? "The owner cannot be removed. Deleting the workspace is the only way out."
                          : isSelf
                            ? "You cannot remove yourself from the workspace."
                            : undefined
                      }
                      className="text-xs px-2 py-1 text-red-600 border border-red-200 rounded hover:bg-red-50 disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
