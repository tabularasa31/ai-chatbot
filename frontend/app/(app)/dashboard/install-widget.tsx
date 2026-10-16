"use client";

import { useState } from "react";
import { CodeBlockWithCopy } from "@/components/ui/code-block-with-copy";
import { CopyButton } from "@/components/ui/copy-button";
import { buildEmbedSnippet } from "@/lib/widget-embed";
import { OptionsReference } from "./options-reference";
import {
  BubbleOptions,
  InlineOptions,
  PlacementChoice,
  type BubblePosition,
  type Placement,
} from "./widget-options";

const DEFAULT_COLOR = "#a855f7";
const DEFAULT_POSITION: BubblePosition = "right";
const DEFAULT_TARGET_ID = "chat9-widget";

function Code({ children }: { children: string }) {
  return <code className="rounded-tag bg-surface-alt px-1 font-mono">{children}</code>;
}

export function InstallWidget({ botId }: { botId: string }) {
  const [placement, setPlacement] = useState<Placement>("bubble");
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [position, setPosition] = useState<BubblePosition>(DEFAULT_POSITION);
  const [targetId, setTargetId] = useState(DEFAULT_TARGET_ID);

  const snippet =
    placement === "bubble"
      ? buildEmbedSnippet({
          botId,
          extraConfig: {
            ...(color !== DEFAULT_COLOR ? { color } : {}),
            ...(position !== DEFAULT_POSITION ? { position } : {}),
          },
        })
      : buildEmbedSnippet({
          botId,
          extraConfig: { mode: "inline", target: targetId },
          extraHtml: `<div id="${targetId}"></div>`,
        });

  return (
    <div className="space-y-8">
      <section>
        <h2 className="mb-3 text-h2 text-fg">Bot ID</h2>
        <div className="flex flex-wrap items-center gap-3">
          <code className="min-w-0 break-all rounded-control bg-surface-alt px-3 py-2 font-mono text-ui text-fg">
            {botId}
          </code>
          <CopyButton text={botId} label="Copy" variant="secondary" />
        </div>
      </section>

      <PlacementChoice placement={placement} onChange={setPlacement} />

      <section>
        {placement === "bubble" ? (
          <BubbleOptions
            color={color}
            onColorChange={setColor}
            position={position}
            onPositionChange={setPosition}
          />
        ) : (
          <InlineOptions targetId={targetId} onTargetIdChange={setTargetId} />
        )}
      </section>

      <section>
        <h2 className="mb-3 text-h2 text-fg">
          {placement === "bubble" ? "Add to your website" : "Add to your page"}
        </h2>
        <p className="mb-3 text-ui text-fg-muted">
          {placement === "bubble" ? (
            <>
              Paste before <Code>&lt;/body&gt;</Code> on every page where you want the chat button.
            </>
          ) : (
            <>
              Place the <Code>&lt;div&gt;</Code> where you want the widget to appear, then paste the script anywhere
              after it.
            </>
          )}
        </p>
        <CodeBlockWithCopy code={snippet} copyLabel="Copy snippet" />
      </section>

      <section>
        <h2 className="mb-3 text-h2 text-fg">Options</h2>
        <OptionsReference placement={placement} />
      </section>
    </div>
  );
}
