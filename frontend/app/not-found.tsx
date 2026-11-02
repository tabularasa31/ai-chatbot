import type { Metadata } from "next";
import { SiteNotFound } from "@/components/marketing/SiteNotFound";

export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false },
};

export default function NotFound() {
  return <SiteNotFound />;
}
