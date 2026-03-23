import { Navbar } from "@/components/Navbar";
import { Sidebar } from "@/components/Sidebar";
import { EmailVerificationGuard } from "@/components/EmailVerificationGuard";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <EmailVerificationGuard>
      <div className="min-h-screen bg-[#F8F9FA]">
        <Navbar />
        <Sidebar />
        <main className="ml-[200px] pt-[calc(48px+32px)] pb-8 px-8 min-h-screen">
          {children}
        </main>
      </div>
    </EmailVerificationGuard>
  );
}
