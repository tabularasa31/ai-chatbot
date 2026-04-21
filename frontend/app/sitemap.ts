import type { MetadataRoute } from "next";
import { getAllBlogPosts } from "@/lib/blog";
import { getSiteUrl } from "@/lib/site";
import { source } from "@/lib/source";

export default function sitemap(): MetadataRoute.Sitemap {
  const siteUrl = getSiteUrl();
  const blogPosts = getAllBlogPosts();
  const docsPages = source.getPages();

  return [
    {
      url: `${siteUrl}/`,
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${siteUrl}/blog`,
      changeFrequency: "weekly",
      priority: 0.8,
    },
    ...blogPosts.map((post) => ({
      url: `${siteUrl}/blog/${post.slug}`,
      lastModified: `${post.publishedAt}T00:00:00Z`,
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
    ...docsPages.map((page) => ({
      url: `${siteUrl}${page.url}`,
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
  ];
}
