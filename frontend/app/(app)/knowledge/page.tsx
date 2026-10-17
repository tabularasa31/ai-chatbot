"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { type KnowledgeTab } from "./_components/shared";
import { KnowledgeFrame } from "./_components/KnowledgeFrame";
import { FaqSection } from "./_components/FaqSection";
import { DocumentsSection } from "./_components/DocumentsSection";
import { ProfileSection } from "./_components/ProfileSection";

function parseTab(value: string | null): KnowledgeTab {
  return value === "faq" || value === "profile" ? value : "documents";
}

export default function KnowledgePage() {
  const router = useRouter();
  const pathname = usePathname();
  // ``useSearchParams()`` would force a Suspense boundary for static prerender
  // (next build fails with "missing-suspense-with-csr-bailout"); reading
  // ``window.location.search`` in an effect keeps the page client-rendered
  // without that wrapper.
  const [activeTab, setActiveTab] = useState<KnowledgeTab>("documents");

  useEffect(() => {
    const syncFromUrl = () => setActiveTab(parseTab(new URLSearchParams(window.location.search).get("tab")));
    syncFromUrl();
    window.addEventListener("popstate", syncFromUrl);
    return () => window.removeEventListener("popstate", syncFromUrl);
  }, []);

  function setTab(tab: KnowledgeTab) {
    const next = new URLSearchParams(window.location.search);
    if (tab === "documents") next.delete("tab");
    else next.set("tab", tab);
    const query = next.toString();
    setActiveTab(tab);
    router.replace(`${pathname}${query ? `?${query}` : ""}`);
  }

  return (
    <KnowledgeFrame activeTab={activeTab} onTabChange={setTab}>
      {activeTab === "profile" && <ProfileSection onGoToDocuments={() => setTab("documents")} />}
      {activeTab === "faq" && <FaqSection onGoToDocuments={() => setTab("documents")} />}
      {activeTab === "documents" && <DocumentsSection />}
    </KnowledgeFrame>
  );
}
