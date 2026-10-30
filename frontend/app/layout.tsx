import type { Metadata } from "next";
import localFont from "next/font/local";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { getMetadataBase } from "@/lib/site";
import { SCHEME_BOOT_SCRIPT, THEME_COLOR_LIGHT } from "@/lib/scheme";
import { TouchActiveEnabler } from "@/components/ui/touch-active";
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
        <meta name="theme-color" content={THEME_COLOR_LIGHT} suppressHydrationWarning />
        <script dangerouslySetInnerHTML={{ __html: SCHEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">
        <PostHogProvider>{children}</PostHogProvider>
        <TouchActiveEnabler />
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
