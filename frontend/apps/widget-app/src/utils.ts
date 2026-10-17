import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { UserHints } from "@chat9/widget-shared";

// Storage-key discriminator derived from hints. Lets us namespace localStorage
// per visitor so one browser shared between accounts doesn't bleed history
// across them. Falls back to email-based synthetic id matching the backend.
export function deriveStorageUserId(hints: UserHints | null | undefined): string | null {
  if (!hints) return null;
  if (hints.user_id && hints.user_id.trim()) return hints.user_id.trim();
  if (hints.email && hints.email.trim()) return `hint:${hints.email.trim()}`;
  return null;
}

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Attach per-bot UTM params to the "Powered by Chat9" link so landing-page
// analytics can attribute referral clicks back to the source bot/tenant.
// botId is the bot's public_id. Returns siteUrl unchanged if it can't be parsed.
export function withUtm(siteUrl: string, botId?: string | null): string {
  try {
    const url = new URL(siteUrl);
    url.searchParams.set("utm_source", "chat9-widget");
    url.searchParams.set("utm_medium", "referral");
    url.searchParams.set("utm_campaign", "powered-by");
    if (botId) url.searchParams.set("utm_content", botId);
    return url.toString();
  } catch {
    return siteUrl;
  }
}
