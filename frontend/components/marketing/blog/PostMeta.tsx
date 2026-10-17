const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

type PostMetaProps = { publishedAt: string; readingTimeMinutes: number };

export function PostMeta({ publishedAt, readingTimeMinutes }: PostMetaProps) {
  return (
    <p className="text-caption tabular-nums text-fg-muted">
      <time dateTime={publishedAt}>{DATE_FORMAT.format(new Date(`${publishedAt}T00:00:00Z`))}</time>
      {" · "}
      {readingTimeMinutes} min read
    </p>
  );
}
