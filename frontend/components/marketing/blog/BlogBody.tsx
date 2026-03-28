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
                className="text-2xl md:text-3xl text-[#FAF5FF] pt-4"
              >
                {block.text}
              </h2>
            );
          case "paragraph":
            return (
              <p
                key={`${block.type}-${index}`}
                className="text-lg leading-8 text-[#FAF5FF]/82"
              >
                {block.text}
              </p>
            );
          case "list":
            return (
              <ul
                key={`${block.type}-${index}`}
                className="space-y-3 pl-6 text-lg leading-8 text-[#FAF5FF]/82 list-disc marker:text-[#38BDF8]"
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
                className="border-l-4 border-[#E879F9] bg-[#12121A] rounded-r-2xl px-6 py-5"
              >
                <p className="text-xl leading-8 text-[#FAF5FF]">{block.text}</p>
                {block.attribution ? (
                  <footer className="mt-3 text-sm uppercase tracking-[0.2em] text-[#FAF5FF]/45">
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
