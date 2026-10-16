import { Navbar } from "@/components/Navbar";
import { Sidebar } from "@/components/Sidebar";
import { DashboardWidgetLoader } from "@/components/DashboardWidgetLoader";
import { ShellBanner } from "@/components/ShellBanner";
import { ForcedLightScheme } from "@/components/ui/forced-light-scheme";
import { getSessionFromCookie } from "@/lib/session";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSessionFromCookie();
  return (
    <div className="min-h-screen bg-canvas">
      <ForcedLightScheme />
      <Navbar initialEmail={session?.email ?? null} />
      <Sidebar />
      <main className="ml-52 min-h-screen px-8 pb-8 pt-20 text-fg">
        <ShellBanner />
        {children}
      </main>
      <DashboardWidgetLoader email={session?.email ?? null} />
    </div>
  );
}
