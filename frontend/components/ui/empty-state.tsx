export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
      {message}
    </div>
  );
}
