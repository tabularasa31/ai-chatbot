import type { Metadata } from "next";
import localFont from "next/font/local";
import { getMetadataBase } from "@/lib/site";
import { SCHEME_BOOT_SCRIPT } from "@/lib/scheme";
import { SchemeBootFallback } from "@/components/ui/scheme-boot-fallback";
import { TouchActiveEnabler } from "@/components/ui/touch-active";
import { PostHogProvider } from "./providers/PostHogProvider";
import { VercelInsights } from "./providers/VercelInsights";
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
    "Chat9 answers support questions from your documentation. Upload files or add your docs site, paste one snippet, and hand off to your team when the bot cannot answer.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: SCHEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">
        <SchemeBootFallback />
        <PostHogProvider>{children}</PostHogProvider>
        <TouchActiveEnabler />
        <VercelInsights />
      </body>
    </html>
  );
}
