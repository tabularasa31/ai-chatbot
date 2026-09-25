import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { rehypeHighlightSubset } from "./highlight";

/** Shared markdown wrapper for assistant/operator/streaming bubbles: same
 *  remark/rehype pipeline and prose classes on every call site. */
export function MarkdownBody({ text, components }: { text: string; components: Components }) {
  return (
    <div className="prose prose-sm max-w-none text-gray-800 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlightSubset]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
