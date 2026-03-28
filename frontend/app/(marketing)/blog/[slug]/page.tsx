import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Footer } from "@/components/marketing/Footer";
import { Navigation } from "@/components/marketing/Navigation";
import { CTABanner } from "@/components/marketing/CTABanner";
import { ImageWithFallback } from "@/components/marketing/figma/ImageWithFallback";
import { BlogBody } from "@/components/marketing/blog/BlogBody";
import { BlogPostCard } from "@/components/marketing/blog/BlogPostCard";
import { getAllBlogPosts, getBlogPostBySlug, getRelatedBlogPosts } from "@/lib/blog";
import { toAbsoluteUrl } from "@/lib/site";

type BlogArticlePageProps = {
  params: {
    slug: string;
  };
};

function getArticleUrl(slug: string): string {
  return toAbsoluteUrl(`/blog/${slug}`);
}

export function generateStaticParams() {
  return getAllBlogPosts().map((post) => ({
    slug: post.slug,
  }));
}

export function generateMetadata({
  params,
}: BlogArticlePageProps): Metadata {
  const post = getBlogPostBySlug(params.slug);

  if (!post) {
    return {};
  }

  const articleUrl = getArticleUrl(post.slug);
  const images = post.coverImage
    ? [
        {
          url: toAbsoluteUrl(post.coverImage),
          alt: post.title,
          width: 1600,
          height: 1600,
        },
      ]
    : undefined;

  return {
    title: `${post.title} | Chat9 Blog`,
    description: post.description,
    alternates: {
      canonical: `/blog/${post.slug}`,
    },
    robots: {
      index: true,
      follow: true,
    },
    openGraph: {
      type: "article",
      title: post.title,
      description: post.description,
      url: articleUrl,
      siteName: "Chat9",
      publishedTime: `${post.publishedAt}T00:00:00Z`,
      images,
    },
    twitter: {
      card: post.coverImage ? "summary_large_image" : "summary",
      title: post.title,
      description: post.description,
      images: post.coverImage ? [toAbsoluteUrl(post.coverImage)] : undefined,
    },
  };
}

export default function BlogArticlePage({ params }: BlogArticlePageProps) {
  const post = getBlogPostBySlug(params.slug);

  if (!post) {
    notFound();
  }

  const relatedPosts = getRelatedBlogPosts(post.slug);
  const articleUrl = getArticleUrl(post.slug);
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: post.title,
    description: post.description,
    url: articleUrl,
    mainEntityOfPage: articleUrl,
    datePublished: post.publishedAt,
    image: post.coverImage ? [toAbsoluteUrl(post.coverImage)] : undefined,
  };

  return (
    <div className="min-h-screen bg-[#0A0A0F] font-['Inter']">
      <Navigation />
      <main>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />

        <article className="mx-auto max-w-4xl px-6 py-16 md:py-24">
          <div className="rounded-3xl border border-[#1E1E2E] bg-[#0F1016] p-8 md:p-12">
            <p className="text-sm uppercase tracking-[0.24em] text-[#38BDF8]">
              Chat9 Blog
            </p>

            <h1 className="mt-6 text-4xl text-[#FAF5FF] md:text-6xl">
              {post.title}
            </h1>

            <div className="mt-6 flex flex-wrap items-center gap-3 text-sm text-[#FAF5FF]/55">
              <span>{post.formattedDate}</span>
              <span className="h-1 w-1 rounded-full bg-[#FAF5FF]/30" />
              <span>{post.readingTimeMinutes} min read</span>
            </div>

            {post.excerpt ? (
              <p className="mt-8 text-xl leading-8 text-[#FAF5FF]/72">
                {post.excerpt}
              </p>
            ) : null}

            <div className="mt-8 flex flex-wrap gap-2">
              {post.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border border-[#1E1E2E] bg-[#12121A] px-3 py-1 text-xs uppercase tracking-[0.18em] text-[#38BDF8]"
                >
                  {tag}
                </span>
              ))}
            </div>

            {post.coverImage ? (
              <div className="mt-10 overflow-hidden rounded-3xl border border-[#1E1E2E]">
                <ImageWithFallback
                  src={post.coverImage}
                  alt={post.title}
                  className="h-auto w-full object-cover"
                />
              </div>
            ) : null}

            <div className="mt-12">
              <BlogBody blocks={post.body} />
            </div>
          </div>
        </article>

        {relatedPosts.length > 0 ? (
          <section className="mx-auto max-w-7xl px-6 pb-8">
            <div className="mb-8 flex items-end justify-between gap-4">
              <div>
                <p className="text-sm uppercase tracking-[0.24em] text-[#38BDF8]">
                  Keep reading
                </p>
                <h2 className="mt-3 text-3xl text-[#FAF5FF]">
                  Related articles
                </h2>
              </div>
            </div>
            <div className="grid gap-6 lg:grid-cols-3">
              {relatedPosts.map((relatedPost) => (
                <BlogPostCard
                  key={relatedPost.slug}
                  post={relatedPost}
                  compact
                />
              ))}
            </div>
          </section>
        ) : null}

        <CTABanner />
      </main>
      <Footer />
    </div>
  );
}
