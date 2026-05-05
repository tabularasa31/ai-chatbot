import { notFound } from 'next/navigation';
import type { Metadata } from 'next';
import { DocsBody, DocsDescription, DocsPage, DocsTitle } from 'fumadocs-ui/page';
import { source } from '@/lib/source';
import { getMDXComponents } from '@/mdx-components';

interface PageProps {
  params: { slug?: string[] };
}

export default async function Page({ params }: PageProps) {
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const MDX = page.data.body;
  // fumadocs-openapi adds an `_openapi` frontmatter block to every generated
  // operation page. Detect it and lift the `.prose` 65 ch max-width so the
  // two-column docs/code-samples layout has room to breathe.
  const isOpenApi = Boolean(page.data._openapi);

  return (
    <DocsPage toc={page.data.toc} full={page.data.full}>
      <DocsTitle>{page.data.title}</DocsTitle>
      {page.data.description ? (
        <DocsDescription>{page.data.description}</DocsDescription>
      ) : null}
      <DocsBody className={isOpenApi ? 'max-w-none' : undefined}>
        <MDX components={getMDXComponents()} />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export function generateMetadata({ params }: PageProps): Metadata {
  const page = source.getPage(params.slug);
  if (!page) return {};

  return {
    title: page.data.title,
    description: page.data.description,
  };
}
