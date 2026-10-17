import type { Metadata } from "next";
import Link from "next/link";
import { MarketingShell } from "@/components/marketing/MarketingShell";
import { BlogPostList } from "@/components/marketing/blog/BlogPostList";
import { CONTAINER, standaloneLinkClasses } from "@/components/marketing/classes";
import { cn } from "@/components/ui/utils";
import { getAllBlogPosts } from "@/lib/blog";

export const metadata: Metadata = {
  title: "Blog | Chat9",
  description:
    "Practical articles on AI chat, support automation, and product decisions for teams evaluating Chat9.",
  alternates: {
    canonical: "/blog",
  },
  robots: {
    index: true,
    follow: true,
  },
  openGraph: {
    title: "Chat9 Blog",
    description:
      "Practical articles on AI chat, support automation, and product decisions for teams evaluating Chat9.",
    url: "/blog",
    siteName: "Chat9",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "Chat9 Blog",
    description:
      "Practical articles on AI chat, support automation, and product decisions for teams evaluating Chat9.",
  },
};

export default function BlogIndexPage() {
  const posts = getAllBlogPosts();

  return (
    <MarketingShell>
      <div className={cn(CONTAINER, "py-12 md:py-16")}>
        <h1 className="text-display text-fg">Blog</h1>
        <div className="mt-8">
          {posts.length > 0 ? (
            <BlogPostList posts={posts} titleAs="h2" />
          ) : (
            <div>
              <p className="text-body text-fg">No articles yet.</p>
              <Link href="/docs" className={standaloneLinkClasses}>
                Read the docs
              </Link>
            </div>
          )}
        </div>
      </div>
    </MarketingShell>
  );
}
