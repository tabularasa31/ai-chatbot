import ReactMarkdown, { type Components, type UrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "./utils";

const SAFE_URL = /^(https?:|mailto:)/i;

const keepSafeUrls: UrlTransform = (url) => (SAFE_URL.test(url) ? url : null);

const COMPONENTS: Components = {
  a: ({ node: _node, children, href, ...props }) =>
    href ? (
      <a {...props} href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-brand underline">
        {children}
      </a>
    ) : (
      <>{children}</>
    ),
  img: () => null,
  ul: ({ node: _node, className, ...props }) => <ul {...props} className={cn("list-disc pl-4", className)} />,
  ol: ({ node: _node, className, ...props }) => <ol {...props} className={cn("list-decimal pl-4", className)} />,
  pre: ({ node: _node, className, ...props }) => (
    <pre
      {...props}
      className={cn(
        "overflow-x-auto rounded-control border border-line bg-surface p-3 font-mono text-caption text-fg",
        className,
      )}
    />
  ),
  code: ({ node: _node, className, children, ...props }) =>
    String(children).includes("\n") ? (
      <code {...props} className={className}>
        {children}
      </code>
    ) : (
      <code {...props} className={cn("rounded-tag border border-line bg-surface px-1 font-mono text-caption", className)}>
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
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS} urlTransform={keepSafeUrls}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
