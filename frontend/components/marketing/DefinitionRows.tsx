import type { ReactNode } from "react";

export type DefinitionRow = { title: string; content: ReactNode };

type DefinitionRowsProps = { list: "ol" | "ul"; rows: DefinitionRow[] };

export function DefinitionRows({ list: List, rows }: DefinitionRowsProps) {
  return (
    <List className="divide-y divide-line">
      {rows.map(({ title, content }) => (
        <li key={title} className="grid gap-2 py-6 first:pt-0 md:grid-cols-12 md:gap-6">
          <h3 className="text-body font-semibold text-fg md:col-span-4">{title}</h3>
          <div className="min-w-0 max-w-measure text-body text-fg md:col-span-8">{content}</div>
        </li>
      ))}
    </List>
  );
}
