import type { BlogPostSummary } from "@/lib/blog/types";

type PostMetaProps = { post: Pick<BlogPostSummary, "publishedAt" | "formattedDate" | "readingTimeMinutes"> };

export function PostMeta({ post }: PostMetaProps) {
  return (
    <p className="text-caption tabular-nums text-fg-muted">
      <time dateTime={post.publishedAt}>{post.formattedDate}</time>
      {" · "}
      {post.readingTimeMinutes} min read
    </p>
  );
}
