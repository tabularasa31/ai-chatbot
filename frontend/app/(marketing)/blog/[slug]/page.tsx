import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { MarketingShell } from "@/components/marketing/MarketingShell";
import { CONTAINER, standaloneLinkClasses, textLinkClasses } from "@/components/marketing/classes";
import { ImageWithFallback } from "@/components/marketing/figma/ImageWithFallback";
import { BlogBody } from "@/components/marketing/blog/BlogBody";
import { BlogPostList } from "@/components/marketing/blog/BlogPostList";
import { PostMeta } from "@/components/marketing/blog/PostMeta";
import { cn } from "@/components/ui/utils";
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
  const socialImage = post.socialImage ?? post.coverImage;
  const images = socialImage
    ? [
        {
          url: toAbsoluteUrl(socialImage),
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
      card: socialImage ? "summary_large_image" : "summary",
      title: post.title,
      description: post.description,
      images: socialImage ? [toAbsoluteUrl(socialImage)] : undefined,
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
  const jsonLdImage = post.socialImage ?? post.coverImage;
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: post.title,
    description: post.description,
    url: articleUrl,
    mainEntityOfPage: articleUrl,
    datePublished: post.publishedAt,
    image: jsonLdImage ? [toAbsoluteUrl(jsonLdImage)] : undefined,
  };

  return (
    <MarketingShell>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <article className={cn(CONTAINER, "pt-8 pb-16 md:pt-12")}>
        <div className="max-w-prose">
          <Link href="/blog" className={cn(standaloneLinkClasses, "gap-2 no-underline hover:underline")}>
            <ArrowLeft size={16} aria-hidden />
            All articles
          </Link>
          <h1 className="mt-6 break-words text-display text-fg">{post.title}</h1>
          <div className="mt-4">
            <PostMeta publishedAt={post.publishedAt} readingTimeMinutes={post.readingTimeMinutes} />
          </div>
          {post.coverImage ? (
            <figure>
              <ImageWithFallback
                src={post.coverImage}
                alt={post.coverAlt ?? ""}
                className="mt-8 h-auto w-full rounded-surface"
              />
            </figure>
          ) : null}
          <div className="mt-8">
            <BlogBody blocks={post.body} />
          </div>
          <p className="mt-12 text-body text-fg">
            Chat9 answers support questions from your own documentation.{" "}
            <Link href="/" className={textLinkClasses}>
              See how it works
            </Link>
          </p>
        </div>
      </article>
      {relatedPosts.length > 0 ? (
        <section className={cn(CONTAINER, "pb-12 md:pb-16")}>
          <div className="max-w-prose border-t border-line pt-8">
            <h2 className="text-h2 text-fg">More articles</h2>
            <div className="mt-4">
              <BlogPostList posts={relatedPosts} titleAs="h3" />
            </div>
          </div>
        </section>
      ) : null}
    </MarketingShell>
  );
}
