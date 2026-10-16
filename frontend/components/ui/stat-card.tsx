export function StatCard({
  label,
  value,
  note,
}: {
  label: string;
  value: string | number;
  note?: string;
}) {
  return (
    <div className="rounded-surface border border-line bg-surface p-4">
      <p className="text-caption font-medium text-fg-muted">{label}</p>
      <p className="mt-2 text-h2 tabular-nums text-fg">{value}</p>
      {note && <p className="mt-1 text-caption text-fg-muted">{note}</p>}
    </div>
  );
}
