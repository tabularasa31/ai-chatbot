import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => (
    // eslint-disable-next-line jsx-a11y/anchor-has-content -- content is spread from react-markdown props at runtime
    <a {...props} target="_blank" rel="noopener noreferrer" className="text-brand underline" />
  ),
  img: () => null,
  ul: ({ node: _node, ...props }) => <ul className="list-disc pl-4" {...props} />,
  ol: ({ node: _node, ...props }) => <ol className="list-decimal pl-4" {...props} />,
  pre: ({ node: _node, ...props }) => (
    <pre
      className="overflow-x-auto rounded-control border border-line bg-surface p-3 font-mono text-caption text-fg"
      {...props}
    />
  ),
  code: ({ node: _node, children, ...props }) =>
    String(children).includes("\n") ? (
      <code {...props}>{children}</code>
    ) : (
      <code className="rounded-tag border border-line bg-surface px-1 font-mono text-caption" {...props}>
        {children}
      </code>
    ),
  table: ({ node: _node, ...props }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-caption" {...props} />
    </div>
  ),
  th: ({ node: _node, ...props }) => (
    <th className="border-b border-line px-2 py-1 text-left font-medium" {...props} />
  ),
  td: ({ node: _node, ...props }) => <td className="border-b border-line px-2 py-1" {...props} />,
};

export function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="space-y-2 break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
