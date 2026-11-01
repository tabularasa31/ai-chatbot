import type { Metadata } from 'next';
import Link from 'next/link';
import { CodeSnippet } from '@/components/marketing/CodeSnippet';
import { DefinitionRows, type DefinitionRow } from '@/components/marketing/DefinitionRows';
import { DemoSlot, HOW_THE_BOT_ANSWERS_HREF } from '@/components/marketing/DemoSlot';
import { MarketingShell } from '@/components/marketing/MarketingShell';
import { PrimaryAction } from '@/components/marketing/PrimaryAction';
import { CONTAINER, standaloneLinkClasses } from '@/components/marketing/classes';
import { cn } from '@/components/ui/utils';
import { getSiteUrl } from '@/lib/site';

const TITLE = 'AI support chatbot for your docs — Chat9';
const DESCRIPTION =
  'Chat9 answers support questions from your documentation. Upload files or add your docs site, paste one snippet, and hand off to your team when the bot cannot answer.';

const EMBED_SNIPPET = `<script src="https://widget.getchat9.live/widget.js" data-bot-id="YOUR_BOT_PUBLIC_ID"></script>
<script>
  Chat9Widget.start();
</script>`;

const SETUP_ROWS: DefinitionRow[] = [
  {
    title: 'Add your documentation',
    content: (
      <p>
        Upload PDF, Markdown, HTML, Word, plain-text or OpenAPI files, or add your docs site by URL. A URL
        source is re-crawled every week unless you change the schedule.
      </p>
    ),
  },
  {
    title: 'Paste the snippet',
    content: (
      <>
        <p>
          Add this before the closing {'</body>'} tag. The widget runs in an iframe, so it cannot read your
          page.
        </p>
        <CodeSnippet code={EMBED_SNIPPET} />
      </>
    ),
  },
  {
    title: 'See what visitors ask',
    content: (
      <p>
        Unanswered lists the questions visitors asked that the bot could not answer, with a drafted reply you
        can edit and publish to the FAQ.
      </p>
    ),
  },
];

const CONDITION_ROWS: DefinitionRow[] = [
  {
    title: 'Cost',
    content: <p>Chat9 is free during early access. OpenAI usage is billed to you directly by OpenAI.</p>,
  },
  {
    title: 'Your OpenAI key',
    content: (
      <p>
        Answers and document embeddings run on your own OpenAI API key, which you add in Settings. Chat9 stores
        it encrypted.
      </p>
    ),
  },
  {
    title: 'Languages',
    content: <p>The bot replies in the language the visitor writes in, and keeps it for the rest of the chat.</p>,
  },
  {
    title: 'Personal data',
    content: (
      <p>
        Emails, phone numbers, card numbers, API keys and IP addresses in a visitor&apos;s message are masked
        before the text reaches OpenAI. Your Inbox shows the original wording.
      </p>
    ),
  },
  {
    title: 'Limits',
    content: <p>Up to 100 documents per account, 50 MB per document, 1,000 characters per visitor message.</p>,
  },
];

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: {
    canonical: '/',
  },
  openGraph: {
    type: 'website',
    url: `${getSiteUrl()}/`,
    siteName: 'Chat9',
    locale: 'en_US',
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: {
    card: 'summary_large_image',
    title: TITLE,
    description: DESCRIPTION,
  },
};

function LandingJsonLd() {
  const siteUrl = getSiteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': `${siteUrl}/#organization`,
        name: 'Chat9',
        url: `${siteUrl}/`,
        description: DESCRIPTION,
      },
      {
        '@type': 'WebSite',
        '@id': `${siteUrl}/#website`,
        url: `${siteUrl}/`,
        name: 'Chat9',
        description: DESCRIPTION,
        publisher: { '@id': `${siteUrl}/#organization` },
      },
      {
        '@type': 'SoftwareApplication',
        name: 'Chat9',
        applicationCategory: 'BusinessApplication',
        operatingSystem: 'Web',
        url: `${siteUrl}/`,
        description: DESCRIPTION,
      },
    ],
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}

const SECTION_GAP = 'space-y-16 md:space-y-24';

export default function LandingPage() {
  const demoBotId = process.env.NEXT_PUBLIC_CHAT9_BOT_ID?.trim();
  if (!demoBotId && process.env.NODE_ENV === 'development') {
    console.warn('NEXT_PUBLIC_CHAT9_BOT_ID is not set: the landing page renders without the live demo.');
  }

  return (
    <MarketingShell>
      <LandingJsonLd />
      <div className={cn(CONTAINER, SECTION_GAP, 'py-12 md:py-16')}>
        <section className="lg:grid lg:grid-cols-12 lg:items-start lg:gap-x-6">
          <div className={cn('lg:col-span-5', !demoBotId && 'max-w-measure lg:col-span-12')}>
            <h1 className="text-display text-fg">Answer support questions from your own documentation</h1>
            <p className="mt-6 max-w-measure text-body text-fg">
              Upload your files or add your docs site, paste one snippet on your website, and the bot answers
              visitors in the language they write in. When it cannot answer, it hands the conversation to your
              team.
            </p>
            <div className="mt-8">
              <PrimaryAction />
            </div>
            <p className="mt-3 text-caption text-fg-muted">
              Free during early access. You bring your own OpenAI API key.
            </p>
            {!demoBotId && (
              <Link href={HOW_THE_BOT_ANSWERS_HREF} className={cn(standaloneLinkClasses, 'mt-2')}>
                See how the bot answers questions
              </Link>
            )}
          </div>
          {demoBotId && (
            <div className="mt-12 lg:col-span-6 lg:col-start-7 lg:mt-0">
              <DemoSlot botId={demoBotId} />
            </div>
          )}
        </section>

        <section>
          <h2 className="mb-8 text-h2 text-fg">From signup to a live widget in 5-10 minutes</h2>
          <DefinitionRows list="ol" rows={SETUP_ROWS} />
        </section>

        <section className="max-w-measure">
          <h2 className="text-h2 text-fg">When the bot cannot answer, your team takes over</h2>
          <p className="mt-4 text-body text-fg">
            If the bot cannot find a confident answer in your documentation, or the visitor asks for a person,
            Chat9 opens a ticket, emails your support address and keeps the chat open.
          </p>
          <p className="mt-4 text-body text-fg">
            In Inbox you see the visitor&apos;s whole history. Take the conversation and reply; the bot stays
            silent while you hold it. If the visitor has not seen your reply after five minutes, Chat9 emails it
            to them.
          </p>
          <Link href="/docs/dashboard-features" className={cn(standaloneLinkClasses, 'mt-2')}>
            How Inbox works
          </Link>
        </section>

        <section>
          <h2 className="mb-8 text-h2 text-fg">Before you sign up</h2>
          <DefinitionRows list="ul" rows={CONDITION_ROWS} />
          <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-2">
            <PrimaryAction />
            <Link href="/docs/getting-started" className={standaloneLinkClasses}>
              Read the getting-started guide
            </Link>
          </div>
        </section>
      </div>
    </MarketingShell>
  );
}
