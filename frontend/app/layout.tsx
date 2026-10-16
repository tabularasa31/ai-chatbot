import type { Metadata } from "next";
import { cookies } from "next/headers";
import localFont from "next/font/local";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { getMetadataBase } from "@/lib/site";
import { SCHEME_BOOT_SCRIPT, SCHEME_COOKIE, parseScheme } from "@/lib/scheme";
import { PostHogProvider } from "./providers/PostHogProvider";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  metadataBase: getMetadataBase(),
  title: "Chat9 — AI support chatbot for your docs",
  description:
    "Chat9 turns your docs into a 24/7 AI support agent. Upload your knowledge base, embed the widget, and let it answer customers automatically.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const scheme = parseScheme(cookies().get(SCHEME_COOKIE)?.value);
  const fontClasses = `${geistSans.variable} ${geistMono.variable}`;
  return (
    <html
      lang="en"
      className={scheme === "dark" ? `${fontClasses} dark` : fontClasses}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: SCHEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">
        <PostHogProvider>{children}</PostHogProvider>
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
