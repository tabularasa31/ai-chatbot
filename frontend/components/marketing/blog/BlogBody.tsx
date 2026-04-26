import type { BlogBlock } from "@/lib/blog/types";

type BlogBodyProps = {
  blocks: BlogBlock[];
};

export function BlogBody({ blocks }: BlogBodyProps) {
  return (
    <div className="space-y-6">
      {blocks.map((block, index) => {
        switch (block.type) {
          case "heading":
            return (
              <h2
                key={`${block.type}-${index}`}
                className="pt-4 text-2xl text-nd-text md:text-3xl"
              >
                {block.text}
              </h2>
            );
          case "paragraph":
            return (
              <p
                key={`${block.type}-${index}`}
                className="text-lg leading-8 text-[#C6C0D4]"
              >
                {block.text}
              </p>
            );
          case "list":
            return (
              <ul
                key={`${block.type}-${index}`}
                className="list-disc space-y-3 pl-6 text-lg leading-8 text-[#C6C0D4] marker:text-[#5EC8FF]"
              >
                {block.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            );
          case "quote":
            return (
              <blockquote
                key={`${block.type}-${index}`}
                className="rounded-r-2xl border-l-4 border-nd-accent bg-nd-base-alt px-6 py-5"
              >
                <p className="text-xl leading-8 text-nd-text">{block.text}</p>
                {block.attribution ? (
                  <footer className="mt-3 text-sm uppercase tracking-[0.2em] text-[#A9A3BA]">
                    {block.attribution}
                  </footer>
                ) : null}
              </blockquote>
            );
        }
      })}
    </div>
  );
}
