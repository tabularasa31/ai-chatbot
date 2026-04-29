"use client";

type LinkSafetyLabels = {
  title: string;
  body: string;
  continue_label: string;
  cancel_label: string;
};

type LinkSafetyModalProps = {
  hostname: string;
  labels: LinkSafetyLabels;
  onConfirm: () => void;
  onCancel: () => void;
};

export function LinkSafetyModal({
  hostname,
  labels,
  onConfirm,
  onCancel,
}: LinkSafetyModalProps) {
  const body = labels.body.includes("{hostname}")
    ? labels.body.replace("{hostname}", hostname)
    : `${labels.body} ${hostname}`;

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="link-safety-title"
        className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 shadow-xl"
      >
        <h2 id="link-safety-title" className="text-base font-semibold text-slate-900">
          {labels.title}
        </h2>
        <p className="mt-2 break-words text-sm leading-6 text-slate-600">{body}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            {labels.cancel_label}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-800"
          >
            {labels.continue_label}
          </button>
        </div>
      </div>
    </div>
  );
}
