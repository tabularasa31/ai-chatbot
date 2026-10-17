import { CopyButton } from "@/components/ui/copy-button";

export function CodeSnippet({ code }: { code: string }) {
  return (
    <div className="mt-4 space-y-3">
      <pre
        tabIndex={0}
        role="region"
        aria-label="Embed snippet"
        className="overflow-x-auto rounded-control bg-surface-alt p-4 font-mono text-ui text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
      >
        {code}
      </pre>
      <CopyButton text={code} label="Copy snippet" variant="secondary" />
    </div>
  );
}
