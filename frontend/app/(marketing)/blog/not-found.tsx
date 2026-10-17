import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { MarketingShell } from "@/components/marketing/MarketingShell";
import { CONTAINER, standaloneLinkClasses } from "@/components/marketing/classes";
import { cn } from "@/components/ui/utils";

export default function BlogNotFound() {
  return (
    <MarketingShell>
      <div className={cn(CONTAINER, "py-12 md:py-16")}>
        <h1 className="text-display text-fg">Article not found</h1>
        <Link href="/blog" className={cn(standaloneLinkClasses, "mt-6 gap-2 no-underline hover:underline")}>
          <ArrowLeft size={16} aria-hidden />
          All articles
        </Link>
      </div>
    </MarketingShell>
  );
}
