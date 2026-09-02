import type { MetadataRoute } from "next";
import { getSiteUrl } from "@/lib/site";

export default function robots(): MetadataRoute.Robots {
  const siteUrl = getSiteUrl();

  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // Keep non-marketing app surfaces out of the index; only /, /blog and
      // /docs should be crawlable. Covers the auth-gated app shells (mirrors
      // middleware PROTECTED_PATHS), the auth/account flows, the embeddable
      // widget host, and the API.
      disallow: [
        "/dashboard",
        "/knowledge",
        "/settings",
        "/widget-settings",
        "/inbox",
        "/admin",
        "/gap-analyzer",
        "/login",
        "/signup",
        "/forgot-password",
        "/reset-password",
        "/verify",
        "/embed",
        "/widget/",
        "/api/",
      ],
    },
    sitemap: `${siteUrl}/sitemap.xml`,
    host: siteUrl,
  };
}
