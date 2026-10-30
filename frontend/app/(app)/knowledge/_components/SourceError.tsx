"use client";

import { Button } from "@/components/ui/button";

function detailsId(sourceId: string): string {
  return `source-error-${sourceId}`;
}

export function SourceError({ sourceId, open, onToggle }: { sourceId: string; open: boolean; onToggle: () => void }) {
  return (
    <div role="presentation" onClick={(event) => event.stopPropagation()}>
      <p className="text-caption text-danger">Couldn&apos;t read this page</p>
      <Button variant="ghost" className="-ml-4" aria-expanded={open} aria-controls={detailsId(sourceId)} onClick={onToggle}>
        {open ? "Hide details" : "Show details"}
      </Button>
    </div>
  );
}

export function SourceErrorDetails({ sourceId, message }: { sourceId: string; message: string }) {
  return (
    <tr className="border-b border-line">
      <td colSpan={5} className="px-4 pb-3">
        <p id={detailsId(sourceId)} className="break-anywhere select-text rounded-control border border-line-strong bg-surface p-3 text-ui text-fg">
          {message}
        </p>
      </td>
    </tr>
  );
}
