import { CopyButton } from "./copy-button";

export function CodeBlockWithCopy({ code, copyLabel }: { code: string; copyLabel: string }) {
  return (
    <div className="space-y-3">
      <pre className="overflow-x-auto whitespace-pre rounded-control bg-surface-alt p-4 font-mono text-ui text-fg">
        {code}
      </pre>
      <CopyButton text={code} label={copyLabel} variant="primary" />
    </div>
  );
}
