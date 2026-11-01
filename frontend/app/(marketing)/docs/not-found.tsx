import type { Metadata } from 'next';
import Link from 'next/link';
import { DocsPage } from 'fumadocs-ui/page';
import { buttonClasses } from '@/components/ui/button';

export const metadata: Metadata = {
  title: 'Page not found',
  robots: { index: false },
};

export default function DocsNotFound() {
  return (
    <DocsPage toc={[]}>
      <h1 className="text-h1 text-fg">Page not found</h1>
      <p className="mt-2 text-body text-fg-muted">This page doesn&apos;t exist or has moved.</p>
      <Link href="/docs" className={`${buttonClasses({ variant: 'primary' })} mt-6 self-start`}>
        Go to documentation home
      </Link>
    </DocsPage>
  );
}
