import Link from "next/link";
import type { BlogPostSummary } from "@/lib/blog/types";
import { PostMeta } from "./PostMeta";

type BlogPostRowProps = { post: BlogPostSummary; titleAs: "h2" | "h3" };

export function BlogPostRow({ post, titleAs: Title }: BlogPostRowProps) {
  return (
    <li className="group relative -mx-4 rounded-control px-4 transition-colors duration-[120ms] hover:bg-surface-alt motion-reduce:transition-none">
      <div className="border-t border-line py-6 group-first:border-t-0">
        <PostMeta post={post} />
        <Title className="mt-2 break-words text-h2 text-fg">
          <Link
            href={`/blog/${post.slug}`}
            className="group-hover:underline focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-focus after:absolute after:inset-0 after:rounded-control"
          >
            {post.title}
          </Link>
        </Title>
        <p className="mt-2 text-body text-fg-muted">{post.excerpt || post.description}</p>
      </div>
    </li>
  );
}
