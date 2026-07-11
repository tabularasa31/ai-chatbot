import { Navbar } from "@/components/Navbar";
import { Sidebar } from "@/components/Sidebar";
import { DashboardWidgetLoader } from "@/components/DashboardWidgetLoader";
import { LlmAlertBanner } from "@/components/LlmAlertBanner";
import { getSessionFromCookie } from "@/lib/session";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSessionFromCookie();
  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <Navbar initialEmail={session?.email ?? null} />
      <Sidebar />
      <main className="ml-[200px] pt-[calc(48px+32px)] pb-8 px-8 min-h-screen">
        <LlmAlertBanner />
        {children}
      </main>
      <DashboardWidgetLoader email={session?.email ?? null} />
    </div>
  );
}
