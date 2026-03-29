import type { Metadata } from "next";
import { Footer } from "@/components/marketing/Footer";
import { Navigation } from "@/components/marketing/Navigation";
import { BlogPostCard } from "@/components/marketing/blog/BlogPostCard";
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
    <div
      className="min-h-screen bg-[#0A0A0F] font-['Inter']"
      style={{ colorScheme: "dark" }}
    >
      <Navigation />
      <main>
        <section className="mx-auto max-w-7xl px-6 py-20 md:py-28">
          <div className="max-w-3xl">
            <p className="text-sm uppercase tracking-[0.24em] text-[#38BDF8]">
              Chat9 Blog
            </p>
            <h1 className="mt-6 text-5xl text-[#FAF5FF] md:text-6xl">
              Clear thinking for AI chat, support, and product teams.
            </h1>
            <p className="mt-6 text-xl leading-8 text-[#AFA8BF]">
              Articles for operators and decision-makers who want practical
              ways to use AI chat without adding complexity.
            </p>
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-6 pb-24">
          <div className="grid gap-6 lg:grid-cols-3">
            {posts.map((post) => (
              <BlogPostCard key={post.slug} post={post} />
            ))}
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
