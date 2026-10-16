import type { ReactNode } from "react";
import type { Placement } from "./widget-options";

function Code({ children }: { children: ReactNode }) {
  return <code className="font-mono text-fg">{children}</code>;
}

export function OptionsReference({ placement }: { placement: Placement }) {
  return (
    <div className="max-w-prose space-y-2 text-ui text-fg-muted">
      <div>
        <Code>data-bot-id</Code> on the script tag — your bot&apos;s public ID (required)
      </div>
      {placement === "bubble" ? (
        <>
          <div>
            <Code>color</Code> — button color, e.g. <Code>#a855f7</Code>
          </div>
          <div>
            <Code>position</Code> — <Code>right</Code> or <Code>left</Code> (default: right)
          </div>
        </>
      ) : (
        <>
          <div>
            <Code>mode</Code> — must be <Code>inline</Code>
          </div>
          <div>
            <Code>target</Code> — ID of the container element (required)
          </div>
        </>
      )}
      <div>
        <Code>userHints</Code> — optional personalization (<Code>name</Code>, <Code>email</Code>,{" "}
        <Code>locale</Code>, <Code>plan_tier</Code>). Untrusted: use for greetings and escalation routing only.
      </div>
      <div className="pt-2">
        All options are passed as the argument to <Code>Chat9Widget.start(...)</Code>. You can also call{" "}
        <Code>Chat9Widget.setHints(hints)</Code> after login to update identity, <Code>Chat9Widget.stop()</Code> to
        unmount, and <Code>Chat9Widget.start()</Code> again to bring it back.
      </div>
    </div>
  );
}
