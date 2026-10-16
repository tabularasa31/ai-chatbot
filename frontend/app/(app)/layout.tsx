import { Navbar } from "@/components/Navbar";
import { Sidebar } from "@/components/Sidebar";
import { DashboardWidgetLoader } from "@/components/DashboardWidgetLoader";
import { LlmAlertBanner } from "@/components/LlmAlertBanner";
import { WidgetIdentityBanner } from "@/components/WidgetIdentityBanner";
import { getSessionFromCookie } from "@/lib/session";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSessionFromCookie();
  return (
    <div className="min-h-screen bg-canvas">
      <Navbar initialEmail={session?.email ?? null} />
      <Sidebar />
      <main className="ml-52 min-h-screen px-8 pb-8 pt-20 text-fg">
        <LlmAlertBanner />
        <WidgetIdentityBanner />
        {children}
      </main>
      <DashboardWidgetLoader email={session?.email ?? null} />
    </div>
  );
}
