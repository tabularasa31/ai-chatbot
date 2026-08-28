"use client";

import { useState } from "react";
import { api, clearSession } from "@/lib/api";

/**
 * The owner's only exit.
 *
 * A workspace has one owner, fixed at creation with no way to hand it over, so
 * deleting it is the only way out — and until now the only door was an
 * undocumented API call. This is that door, with the consequences written down
 * next to it.
 *
 * The confirmation is the workspace's name typed out, not a red button. What
 * this destroys is not the owner's own work but every conversation their
 * visitors ever had, and a button is something you can click by accident on
 * the way to something else.
 *
 * The warning names the consequences that are not obvious. Losing the
 * conversations is expected; that the widget stops answering on their site,
 * and that they are signed out of an account they can no longer sign back into,
 * are the two that arrive as a surprise on a Monday morning.
 *
 * It says "everything we hold for this workspace" rather than "nowhere does it
 * remain", deliberately. Deleting the source does not reach copies already
 * taken out of it — an export, a screenshot, a dataset someone built. The
 * second phrasing would be a promise nobody can keep.
 */
export default function DeleteWorkspaceCard({
  workspaceName,
  workspaceId,
}: {
  workspaceName: string;
  workspaceId: string;
}) {
  const [open, setOpen] = useState(false);
  const [typedName, setTypedName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  const nameMatches = typedName.trim() === workspaceName.trim();

  async function deleteWorkspace() {
    if (!nameMatches) return;
    setError("");
    setDeleting(true);
    try {
      await api.clients.delete(workspaceId);
      // The owner's account went with the workspace, so there is no session
      // left and nothing on this page can be refetched. Leave immediately
      // rather than let a background request discover the 401 and report it
      // as an expired session.
      clearSession();
      window.location.replace("/login?deleted=workspace");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete the workspace");
      setDeleting(false);
    }
  }

  return (
    <div className="bg-white rounded-xl border border-red-200 p-6 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-red-700">Delete this workspace</h2>
        <p className="text-sm text-slate-500 mt-1">
          A workspace has one owner, and ownership cannot be handed over.
          Deleting it is the only way to leave.
        </p>
      </div>

      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="px-4 py-2 border border-red-300 text-red-700 text-sm font-medium rounded-lg hover:bg-red-50 transition-colors"
        >
          Delete workspace…
        </button>
      )}

      {open && (
        <div className="space-y-4">
          <div className="rounded-lg border border-red-100 bg-red-50/60 px-4 py-3 space-y-2">
            <p className="text-sm font-medium text-red-800">
              This happens immediately and cannot be undone. There is no grace
              period and no way for us to restore it.
            </p>
            <ul className="text-sm text-red-800/90 space-y-1.5 list-disc pl-5">
              <li>
                <span className="font-medium">Your widget stops answering.</span>{" "}
                Visitors on your site get nothing from it from the moment you
                confirm.
              </li>
              <li>
                Every conversation your visitors have had is deleted, along with
                escalation tickets, uploaded documents and everything indexed
                from them.
              </li>
              <li>
                <span className="font-medium">Your own account goes with it.</span>{" "}
                You are signed out, and there is no account left to sign back
                in with. The same is true for every colleague you invited.
              </li>
              <li>
                Your API keys stop working, and anything built against them
                stops with them.
              </li>
            </ul>
            <p className="text-sm text-red-800/90">
              This removes everything we hold for this workspace, in our
              database and in the systems we send it to. It cannot reach copies
              already taken out of them — an export you downloaded, a
              screenshot, a report someone saved.
            </p>
          </div>

          <div className="space-y-2">
            <label
              htmlFor="delete-workspace-name"
              className="block text-sm text-slate-700"
            >
              Type{" "}
              <span className="font-mono font-medium text-slate-900">
                {workspaceName}
              </span>{" "}
              to confirm.
            </label>
            <input
              id="delete-workspace-name"
              type="text"
              autoComplete="off"
              value={typedName}
              onChange={(e) => setTypedName(e.target.value)}
              placeholder={workspaceName}
              aria-label="Workspace name confirmation"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 outline-none focus:border-red-400 placeholder:text-slate-300"
            />
          </div>

          {error && <p className="text-red-600 text-sm">{error}</p>}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={deleteWorkspace}
              disabled={!nameMatches || deleting}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              {deleting ? "Deleting…" : "Delete this workspace permanently"}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setTypedName("");
                setError("");
              }}
              disabled={deleting}
              className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg disabled:opacity-40 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
