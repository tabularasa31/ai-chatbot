import Link from "next/link";
import type { BlogPostSummary } from "@/lib/blog/types";

type BlogPostCardProps = {
  post: BlogPostSummary;
  compact?: boolean;
};

export function BlogPostCard({
  post,
  compact = false,
}: BlogPostCardProps) {
  return (
    <article className="h-full rounded-2xl border border-[#2E2E46] bg-[#1A1A26] p-6 shadow-[0_0_0_1px_rgba(255,255,255,0.02)] transition-colors hover:border-[#5EC8FF]/55 hover:bg-[#202030]">
      <div className="flex flex-wrap items-center gap-3 text-sm text-[#A9A3BA]">
        <span>{post.formattedDate}</span>
        <span className="h-1 w-1 rounded-full bg-[#FAF5FF]/30" />
        <span>{post.readingTimeMinutes} min read</span>
      </div>

      <h2
        className={`mt-4 text-[#E6E0F0] ${
          compact ? "text-2xl" : "text-3xl"
        }`}
      >
        <Link href={`/blog/${post.slug}`} className="hover:text-[#38BDF8] transition-colors">
          {post.title}
        </Link>
      </h2>

      <p className="mt-4 text-base leading-7 text-[#AFA8BF]">
        {post.excerpt || post.description}
      </p>

      <div className="mt-5 flex flex-wrap gap-2">
        {post.tags.map((tag) => (
          <span
            key={tag}
            className="rounded-full border border-[#32405F] bg-[#101722] px-3 py-1 text-xs uppercase tracking-[0.18em] text-[#74D0FF]"
          >
            {tag}
          </span>
        ))}
      </div>

      <Link
        href={`/blog/${post.slug}`}
        className="mt-6 inline-flex items-center text-sm font-medium text-[#F3A9FF] transition-colors hover:text-[#FFD0FF]"
      >
        Read article
      </Link>
    </article>
  );
}
