import { CopyButton } from "@/components/ui/copy-button";

export function CodeSnippet({ code }: { code: string }) {
  return (
    <div className="mt-4 space-y-3">
      <pre className="whitespace-pre-wrap break-words rounded-control bg-surface-alt p-4 font-mono text-ui text-fg">
        {code}
      </pre>
      <CopyButton text={code} label="Copy snippet" variant="secondary" />
    </div>
  );
}
