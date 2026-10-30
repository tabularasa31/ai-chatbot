import type { DocumentHealthStatus, DocumentListItem, UrlSource } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { SourceError } from "./SourceError";
import { sourceStatus, type StatusDot } from "./shared";

const GOOD_SCORE = 80;
const FAIR_SCORE = 50;

const DOT_CLASS = { danger: "bg-danger", warning: "bg-warning" } as const;

function Labelled({ dot, children }: { dot: StatusDot; children: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      {dot && <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${DOT_CLASS[dot]}`} />}
      {children}
    </span>
  );
}

function Caption({ children }: { children: string }) {
  return <p className="break-anywhere text-caption text-fg-muted">{children}</p>;
}

function HealthLines({ health }: { health: DocumentHealthStatus | null | undefined }) {
  if (health == null) return <Caption>Checking health</Caption>;
  if (health.error || health.score === null) return <Caption>Health check unavailable</Caption>;
  if (health.score >= GOOD_SCORE) return null;

  const needsAttention = health.score < FAIR_SCORE;
  return (
    <div className="mt-1 space-y-0.5">
      <p className="text-caption text-fg">
        <Labelled dot={needsAttention ? "danger" : "warning"}>{needsAttention ? "Needs attention" : "Fair"}</Labelled>
      </p>
      {health.warnings.map((warning) => (
        <Caption key={warning.message}>{warning.message}</Caption>
      ))}
      <Caption>{`Checked ${formatDateTime(health.checked_at)}`}</Caption>
    </div>
  );
}

export function FileStatus({ doc }: { doc: DocumentListItem }) {
  const { label, dot } = sourceStatus(doc.status);
  return (
    <div className="text-ui text-fg">
      <Labelled dot={dot}>{label}</Labelled>
      {doc.status === "ready" && <HealthLines health={doc.health_status} />}
    </div>
  );
}

export function UrlStatus({ source }: { source: UrlSource }) {
  const { label, dot } = sourceStatus(source.status);
  return (
    <div className="text-ui text-fg">
      <Labelled dot={dot}>{label}</Labelled>
      <div className="mt-1 space-y-0.5">
        {source.status === "paused" && <Caption>Needs your action before indexing continues.</Caption>}
        {source.error_message && <SourceError sourceId={source.id} message={source.error_message} />}
        {source.warning_message && <Caption>{source.warning_message}</Caption>}
      </div>
    </div>
  );
}
