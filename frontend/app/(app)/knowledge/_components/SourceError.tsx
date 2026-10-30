"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

export function SourceError({ sourceId, message }: { sourceId: string; message: string }) {
  const [open, setOpen] = useState(false);
  const detailsId = `source-error-${sourceId}`;
  return (
    <div role="presentation" onClick={(event) => event.stopPropagation()}>
      <p className="text-caption text-danger">Couldn&apos;t read this page</p>
      <Button
        variant="ghost"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen((current) => !current)}
      >
        {open ? "Hide details" : "Show details"}
      </Button>
      {open && (
        <p id={detailsId} className="break-anywhere select-text rounded-control bg-surface-alt p-3 text-ui text-fg">
          {message}
        </p>
      )}
    </div>
  );
}
