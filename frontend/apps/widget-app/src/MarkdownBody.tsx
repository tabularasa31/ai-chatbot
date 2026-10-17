import type { Element, Root } from "hast";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";
import { rehypeHighlightSubset } from "./highlight";

const AUTO_DIRECTION_TAGS = new Set(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"]);

// Mixed-language replies: each block picks its own text direction.
const rehypeAutoDirection: Plugin<[], Root> = () => (tree: Root) => {
  visit(tree, "element", (node: Element) => {
    if (AUTO_DIRECTION_TAGS.has(node.tagName)) node.properties.dir = "auto";
  });
};

/** Shared markdown wrapper for assistant/operator/streaming bubbles: same
 *  remark/rehype pipeline and element styles (`.md-body`) on every call site. */
export function MarkdownBody({ text, components }: { text: string; components: Components }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlightSubset, rehypeAutoDirection]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
