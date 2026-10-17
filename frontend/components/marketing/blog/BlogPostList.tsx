import type { BlogPostSummary } from "@/lib/blog/types";
import { BlogPostRow } from "./BlogPostRow";

type BlogPostListProps = { posts: BlogPostSummary[]; titleAs: "h2" | "h3" };

export function BlogPostList({ posts, titleAs }: BlogPostListProps) {
  return (
    <ul className="max-w-prose divide-y divide-line">
      {posts.map((post) => (
        <BlogPostRow key={post.slug} post={post} titleAs={titleAs} />
      ))}
    </ul>
  );
}
