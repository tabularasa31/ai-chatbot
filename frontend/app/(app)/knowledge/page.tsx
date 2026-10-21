"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { type KnowledgeTab } from "./_components/shared";
import { KnowledgeFrame } from "./_components/KnowledgeFrame";
import { FaqSection } from "./_components/FaqSection";
import { DocumentsSection } from "./_components/DocumentsSection";
import { ProfileSection } from "./_components/ProfileSection";
import { UnansweredSection, parseShow, type UnansweredShow } from "./_components/UnansweredSection";

function parseTab(value: string | null): KnowledgeTab {
  return value === "faq" || value === "profile" || value === "unanswered" ? value : "documents";
}

export default function KnowledgePage() {
  const router = useRouter();
  const pathname = usePathname();
  // ``useSearchParams()`` would force a Suspense boundary for static prerender
  // (next build fails with "missing-suspense-with-csr-bailout"); reading
  // ``window.location.search`` in an effect keeps the page client-rendered
  // without that wrapper.
  const [activeTab, setActiveTab] = useState<KnowledgeTab>("documents");
  const [show, setShow] = useState<UnansweredShow>("open");

  useEffect(() => {
    const syncFromUrl = () => {
      const params = new URLSearchParams(window.location.search);
      setActiveTab(parseTab(params.get("tab")));
      setShow(parseShow(params.get("show")));
    };
    syncFromUrl();
    window.addEventListener("popstate", syncFromUrl);
    return () => window.removeEventListener("popstate", syncFromUrl);
  }, []);

  function setTab(tab: KnowledgeTab) {
    const next = new URLSearchParams(window.location.search);
    if (tab === "documents") next.delete("tab");
    else next.set("tab", tab);
    next.delete("show");
    replaceQuery(next);
    setActiveTab(tab);
    setShow("open");
  }

  function changeShow(value: UnansweredShow) {
    const next = new URLSearchParams(window.location.search);
    if (value === "open") next.delete("show");
    else next.set("show", value);
    replaceQuery(next);
    setShow(value);
  }

  function replaceQuery(params: URLSearchParams) {
    const query = params.toString();
    router.replace(`${pathname}${query ? `?${query}` : ""}`);
  }

  return (
    <KnowledgeFrame activeTab={activeTab} onTabChange={setTab}>
      {activeTab === "profile" && <ProfileSection onGoToDocuments={() => setTab("documents")} />}
      {activeTab === "faq" && <FaqSection onGoToDocuments={() => setTab("documents")} />}
      {activeTab === "unanswered" && <UnansweredSection show={show} onShowChange={changeShow} />}
      {activeTab === "documents" && <DocumentsSection />}
    </KnowledgeFrame>
  );
}
