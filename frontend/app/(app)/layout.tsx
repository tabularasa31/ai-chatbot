import { Navbar } from "@/components/Navbar";
import { Sidebar } from "@/components/Sidebar";
import { DashboardWidgetLoader } from "@/components/DashboardWidgetLoader";
import { LlmAlertBanner } from "@/components/LlmAlertBanner";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <Navbar />
      <Sidebar />
      <main className="ml-[200px] pt-[calc(48px+32px)] pb-8 px-8 min-h-screen">
        <LlmAlertBanner />
        {children}
      </main>
      <DashboardWidgetLoader />
    </div>
  );
}
