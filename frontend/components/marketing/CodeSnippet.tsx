import { CopyButton } from "@/components/ui/copy-button";

export function CodeSnippet({ code }: { code: string }) {
  return (
    <div className="mt-4 space-y-3">
      <div
        role="textbox"
        aria-readonly="true"
        aria-multiline="true"
        tabIndex={0}
        aria-label="Embed snippet"
        className="overflow-x-auto rounded-control bg-surface-alt p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
      >
        <pre className="font-mono text-ui text-fg">{code}</pre>
      </div>
      <CopyButton text={code} label="Copy snippet" variant="secondary" />
    </div>
  );
}
