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
    <article className="h-full rounded-2xl border border-[#1E1E2E] bg-[#12121A] p-6 transition-colors hover:border-[#38BDF8]/40 hover:bg-[#171723]">
      <div className="flex flex-wrap items-center gap-3 text-sm text-[#FAF5FF]/55">
        <span>{post.formattedDate}</span>
        <span className="h-1 w-1 rounded-full bg-[#FAF5FF]/30" />
        <span>{post.readingTimeMinutes} min read</span>
      </div>

      <h2
        className={`mt-4 text-[#FAF5FF] ${
          compact ? "text-2xl" : "text-3xl"
        }`}
      >
        <Link href={`/blog/${post.slug}`} className="hover:text-[#38BDF8] transition-colors">
          {post.title}
        </Link>
      </h2>

      <p className="mt-4 text-base leading-7 text-[#FAF5FF]/72">
        {post.excerpt || post.description}
      </p>

      <div className="mt-5 flex flex-wrap gap-2">
        {post.tags.map((tag) => (
          <span
            key={tag}
            className="rounded-full border border-[#1E1E2E] bg-[#0A0A0F] px-3 py-1 text-xs uppercase tracking-[0.18em] text-[#38BDF8]"
          >
            {tag}
          </span>
        ))}
      </div>

      <Link
        href={`/blog/${post.slug}`}
        className="mt-6 inline-flex items-center text-sm font-medium text-[#E879F9] transition-colors hover:text-[#f099fb]"
      >
        Read article
      </Link>
    </article>
  );
}
