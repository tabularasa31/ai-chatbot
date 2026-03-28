import type { BlogBlock, BlogPost, BlogPostSummary } from "@/lib/blog/types";
import { howToAddAnAiChatbotToYourWebsiteIn2026 } from "@/lib/blog/posts/how-to-add-an-ai-chatbot-to-your-website-in-2026";
import { howChat9AiBotWorks } from "@/lib/blog/posts/how-chat9-ai-bot-works";
import { ragVsFineTuningWhatActuallyMattersForYourBusiness } from "@/lib/blog/posts/rag-vs-fine-tuning-what-actually-matters-for-your-business";
import { reducingSupportCostsWithAiChatPracticalGuide } from "@/lib/blog/posts/reducing-support-costs-with-ai-chat-practical-guide";

const WORDS_PER_MINUTE = 200;
const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const VALID_BLOCK_TYPES = new Set(["paragraph", "heading", "list", "quote"]);

const rawPosts: BlogPost[] = [
  howChat9AiBotWorks,
  howToAddAnAiChatbotToYourWebsiteIn2026,
  ragVsFineTuningWhatActuallyMattersForYourBusiness,
  reducingSupportCostsWithAiChatPracticalGuide,
];

function assertValidDate(date: string, slug: string): void {
  if (!DATE_ONLY_PATTERN.test(date)) {
    throw new Error(
      `Blog post "${slug}" has invalid publishedAt "${date}". Expected YYYY-MM-DD.`,
    );
  }

  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`Blog post "${slug}" has an unreadable publishedAt value.`);
  }
}

function assertValidBody(body: BlogBlock[], slug: string): void {
  if (!Array.isArray(body) || body.length === 0) {
    throw new Error(`Blog post "${slug}" must include at least one body block.`);
  }

  body.forEach((block, index) => {
    if (!VALID_BLOCK_TYPES.has(block.type)) {
      throw new Error(
        `Blog post "${slug}" has unsupported block type "${String(
          (block as { type?: unknown }).type,
        )}" at index ${index}.`,
      );
    }

    if (block.type === "list" && block.items.length === 0) {
      throw new Error(
        `Blog post "${slug}" has an empty list block at index ${index}.`,
      );
    }
  });
}

function assertValidPost(post: BlogPost, seenSlugs: Set<string>): void {
  if (!SLUG_PATTERN.test(post.slug)) {
    throw new Error(
      `Blog post "${post.title}" has invalid slug "${post.slug}". Expected lowercase kebab-case.`,
    );
  }

  if (seenSlugs.has(post.slug)) {
    throw new Error(`Duplicate blog slug detected: "${post.slug}".`);
  }

  seenSlugs.add(post.slug);
  assertValidDate(post.publishedAt, post.slug);
  assertValidBody(post.body, post.slug);
}

function formatPublishedDate(date: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T00:00:00Z`));
}

function getPlainTextFromBlock(block: BlogBlock): string {
  switch (block.type) {
    case "paragraph":
    case "heading":
    case "quote":
      return block.text;
    case "list":
      return block.items.join(" ");
  }
}

export function getPlainTextFromPost(post: BlogPost): string {
  return post.body.map(getPlainTextFromBlock).join(" ").trim();
}

export function getReadingTimeMinutes(post: BlogPost): number {
  const wordCount = getPlainTextFromPost(post)
    .split(/\s+/)
    .filter(Boolean).length;

  return Math.max(1, Math.ceil(wordCount / WORDS_PER_MINUTE));
}

const seenSlugs = new Set<string>();
rawPosts.forEach((post) => assertValidPost(post, seenSlugs));

const sortedPosts = [...rawPosts].sort((a, b) => {
  if (a.publishedAt !== b.publishedAt) {
    return b.publishedAt.localeCompare(a.publishedAt);
  }

  return a.title.localeCompare(b.title);
});

export const blogPosts: BlogPostSummary[] = sortedPosts.map((post) => ({
  ...post,
  formattedDate: formatPublishedDate(post.publishedAt),
  readingTimeMinutes: getReadingTimeMinutes(post),
}));

export function getAllBlogPosts(): BlogPostSummary[] {
  return blogPosts;
}

export function getBlogPostBySlug(slug: string): BlogPostSummary | undefined {
  return blogPosts.find((post) => post.slug === slug);
}

function getSharedTagScore(currentPost: BlogPost, candidate: BlogPost): number {
  const currentTags = new Set(currentPost.tags);
  return candidate.tags.reduce(
    (score, tag) => score + (currentTags.has(tag) ? 1 : 0),
    0,
  );
}

export function getRelatedBlogPosts(
  currentSlug: string,
  limit = 3,
): BlogPostSummary[] {
  const currentPost = getBlogPostBySlug(currentSlug);
  if (!currentPost) return [];

  return blogPosts
    .filter((post) => post.slug !== currentSlug)
    .sort((a, b) => {
      const sharedTagDiff =
        getSharedTagScore(currentPost, b) - getSharedTagScore(currentPost, a);
      if (sharedTagDiff !== 0) return sharedTagDiff;

      const publishedDiff = b.publishedAt.localeCompare(a.publishedAt);
      if (publishedDiff !== 0) return publishedDiff;

      return a.title.localeCompare(b.title);
    })
    .slice(0, limit);
}
