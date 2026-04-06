"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type NavItem = {
  href: string;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
};

const mainNav: NavItem[] = [
  {
    href: "/dashboard",
    label: "Dashboard",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <rect x="1" y="1" width="5.5" height="5.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
        <rect x="8.5" y="1" width="5.5" height="5.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
        <rect x="1" y="8.5" width="5.5" height="5.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
        <rect x="8.5" y="8.5" width="5.5" height="5.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
  {
    href: "/knowledge",
    label: "Knowledge Hub",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 1L13 4v7l-5.5 3L2 11V4L7.5 1z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
        <path d="M7.5 1v13M2 4l5.5 3 5.5-3" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    href: "/logs",
    label: "Logs",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M2 3h11M2 7h11M2 11h7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: "/review",
    label: "Review",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M2.5 7.5L5.5 10.5L12.5 3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    href: "/escalations",
    label: "Escalations",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 2v7M7.5 12v1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="7.5" cy="12.5" r="0.75" fill="currentColor" />
      </svg>
    ),
  },
  {
    href: "/debug",
    label: "Debug",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M5 2.5C5 1.67 5.67 1 6.5 1h2c.83 0 1.5.67 1.5 1.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        <path d="M3 6h9M3 9h9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        <rect x="3" y="4" width="9" height="8" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
];

const settingsNav: NavItem[] = [
  {
    href: "/settings",
    label: "Settings",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M2 4h11M2 7.5h7M2 11h5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        <circle cx="12" cy="11" r="1.8" stroke="currentColor" strokeWidth="1.3" />
        <path d="M12 9.2V8M12 13.8V13" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        <path d="M10.33 9.67l-.85-.85M14.52 13.18l-.85-.85M10.33 12.33l-.85.85M14.52 9.82l-.85.85" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: "/widget-settings",
    label: "Widget",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M4.5 5L2 7.5 4.5 10M10.5 5L13 7.5 10.5 10M7.5 4l-1.5 7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
];

const adminNav: NavItem[] = [
  {
    href: "/admin/metrics",
    label: "Admin",
    adminOnly: true,
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 1l1.545 3.13L12.5 4.635l-2.5 2.435.59 3.43L7.5 8.885 4.41 10.5l.59-3.43-2.5-2.435 3.455-.505L7.5 1z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      </svg>
    ),
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    api.clients.getMe().then((c) => setIsAdmin(c.is_admin)).catch(() => {});
  }, []);

  function isActive(href: string) {
    if (href === "/dashboard") return pathname === "/dashboard";
    if (href === "/settings") return pathname === "/settings";
    return pathname === href || pathname.startsWith(href + "/");
  }

  function NavLink({ item }: { item: NavItem }) {
    return (
      <Link
        href={item.href}
        className={`flex items-center gap-2.5 px-3 py-[7px] rounded-md text-[13px] relative transition-colors ${
          isActive(item.href)
            ? "text-[#FAF5FF] bg-[#E879F9]/[0.08]"
            : "text-[#FAF5FF]/50 hover:text-[#FAF5FF]/80 hover:bg-white/[0.03]"
        }`}
      >
        {isActive(item.href) && (
          <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-[#E879F9]" />
        )}
        <span className={isActive(item.href) ? "opacity-90" : "opacity-60"}>
          {item.icon}
        </span>
        {item.label}
      </Link>
    );
  }

  return (
    <aside
      className="fixed top-12 left-0 h-[calc(100vh-48px)] w-[200px] flex flex-col py-4"
      style={{ backgroundColor: "#0A0A0F", borderRight: "1px solid rgba(255,255,255,0.07)" }}
    >
      <div className="flex flex-col gap-0.5 px-3">
        {mainNav.map((item) => (
          <NavLink key={item.href} item={item} />
        ))}
      </div>

      <div className="mx-4 my-3 border-t border-white/[0.07]" />

      <div className="px-3 mb-1">
        <p className="text-[10px] uppercase tracking-widest text-[#FAF5FF]/20 px-3 mb-1.5">
          Configure
        </p>
        <div className="flex flex-col gap-0.5">
          {settingsNav.map((item) => (
            <NavLink key={item.href} item={item} />
          ))}
        </div>
      </div>

      {isAdmin && (
        <>
          <div className="mx-4 my-3 border-t border-white/[0.07]" />
          <div className="px-3">
            {adminNav.map((item) => (
              <NavLink key={item.href} item={item} />
            ))}
          </div>
        </>
      )}
    </aside>
  );
}
