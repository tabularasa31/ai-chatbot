import Link from "next/link";
import { Button } from "@/components/ui/button";
import { InlineNotice } from "@/components/ui/inline-notice";
import { LINK_CLASS } from "./link-class";
import type { DraftNotice as Notice } from "./useAnswerDraft";

export function DraftNotice({
  notice,
  isOwner,
  canRetry,
  onRetry,
  onReload,
}: {
  notice: Notice;
  isOwner: boolean;
  canRetry: boolean;
  onRetry: () => void;
  onReload: () => void;
}) {
  const retry = canRetry ? (
    <Button variant="secondary" offset="surface" className="self-center" onClick={onRetry}>
      Try again
    </Button>
  ) : undefined;

  switch (notice.kind) {
    case "no_key":
      return (
        <InlineNotice tone="warning">
          No OpenAI key is set, so a draft can&apos;t be generated. Write the answer yourself or add a key in{" "}
          {isOwner ? (
            <Link href="/settings" className={LINK_CLASS}>
              Settings
            </Link>
          ) : (
            "Settings"
          )}
          .
        </InlineNotice>
      );
    case "generation_failed":
      return (
        <InlineNotice tone="danger" action={retry}>
          Couldn&apos;t generate a draft. Try again or write the answer yourself.
        </InlineNotice>
      );
    case "rate_limited":
      return (
        <InlineNotice tone="warning" action={retry}>
          Too many requests to the model. Wait a minute and try again.
        </InlineNotice>
      );
    case "refine_rejected":
      return (
        <InlineNotice tone="warning">
          The rewrite was rejected twice, so your draft is unchanged. Change the instructions or edit the answer
          yourself.
        </InlineNotice>
      );
    case "refine_failed":
      return <InlineNotice tone="danger">Couldn&apos;t rewrite the answer. Try again.</InlineNotice>;
    case "save_failed":
      return <InlineNotice tone="danger">Couldn&apos;t save the draft. Try again.</InlineNotice>;
    case "conflict":
      return (
        <InlineNotice
          tone="danger"
          action={
            <Button variant="secondary" offset="surface" className="self-center" onClick={onReload}>
              Reload draft
            </Button>
          }
        >
          Someone else changed this draft. Your edits are not saved.
        </InlineNotice>
      );
    case "reloaded":
      return <InlineNotice>Draft reloaded. Your previous text is below.</InlineNotice>;
  }
}
