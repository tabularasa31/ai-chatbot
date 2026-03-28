export const SITE_URL =
  process.env.NEXT_PUBLIC_APP_URL?.trim() || "https://getchat9.live";

export function getSiteUrl(): string {
  return SITE_URL.replace(/\/+$/, "");
}

export function getMetadataBase(): URL {
  return new URL(getSiteUrl());
}

export function toAbsoluteUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${getSiteUrl()}${normalizedPath}`;
}
