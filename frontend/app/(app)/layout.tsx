import { Navbar } from "@/components/Navbar";
import { Sidebar } from "@/components/Sidebar";
import { DashboardWidgetLoader } from "@/components/DashboardWidgetLoader";
import { ShellBanner } from "@/components/ShellBanner";
import { SchemeSurface } from "@/components/ui/scheme-surface";
import { getSessionFromCookie } from "@/lib/session";
import { prefetchShellState } from "@/lib/shell-state";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [session, shellState] = await Promise.all([getSessionFromCookie(), prefetchShellState()]);
  return (
    <SchemeSurface>
      <Navbar initialEmail={session?.email ?? null} />
      <Sidebar />
      <main className="ml-52 flex min-h-screen flex-col px-8 pb-8 pt-20 text-fg">
        <ShellBanner initialState={shellState} />
        {children}
      </main>
      <DashboardWidgetLoader email={session?.email ?? null} />
    </SchemeSurface>
  );
}
