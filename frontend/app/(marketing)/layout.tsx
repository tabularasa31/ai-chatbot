import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Chat9 - Your support mate, always on',
  description: 'Meet your new support mate. Works 24/7. Sends you a daily report. Gets better every week.',
};

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
