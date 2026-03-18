"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken, saveToken } from "@/lib/api";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    const token = getToken();
    if (token) {
      saveToken(token);
      router.replace("/dashboard");
    } else {
      router.replace("/login");
    }
  }, [router]);

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center">
      <div className="animate-pulse text-slate-600">Loading...</div>
    </div>
  );
}
