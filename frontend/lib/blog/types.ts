export type BlogParagraphBlock = {
  type: "paragraph";
  text: string;
};

export type BlogHeadingBlock = {
  type: "heading";
  text: string;
};

export type BlogListBlock = {
  type: "list";
  items: string[];
};

export type BlogQuoteBlock = {
  type: "quote";
  text: string;
  attribution?: string;
};

export type BlogBlock =
  | BlogParagraphBlock
  | BlogHeadingBlock
  | BlogListBlock
  | BlogQuoteBlock;

export type BlogPost = {
  slug: string;
  title: string;
  description: string;
  excerpt?: string;
  publishedAt: string;
  coverImage?: string;
  tags: string[];
  body: BlogBlock[];
};

export type BlogPostSummary = BlogPost & {
  readingTimeMinutes: number;
  formattedDate: string;
};
