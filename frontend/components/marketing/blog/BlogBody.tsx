import type { BlogBlock } from "@/lib/blog/types";

type BlogBodyProps = {
  blocks: BlogBlock[];
};

export function BlogBody({ blocks }: BlogBodyProps) {
  return (
    <div className="flex flex-col gap-6 text-body text-fg">
      {blocks.map((block, index) => {
        switch (block.type) {
          case "heading":
            return (
              <h2 key={`${block.type}-${index}`} className="mt-6 -mb-2 text-h2">
                {block.text}
              </h2>
            );
          case "paragraph":
            return <p key={`${block.type}-${index}`}>{block.text}</p>;
          case "list":
            return (
              <ul key={`${block.type}-${index}`} className="list-disc space-y-2 pl-6 marker:text-fg-muted">
                {block.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            );
          case "quote":
            return (
              <blockquote key={`${block.type}-${index}`} className="border-l-2 border-line-strong pl-4">
                <p>{block.text}</p>
                {block.attribution ? (
                  <footer className="mt-2 text-caption text-fg-muted">— {block.attribution}</footer>
                ) : null}
              </blockquote>
            );
        }
      })}
    </div>
  );
}
