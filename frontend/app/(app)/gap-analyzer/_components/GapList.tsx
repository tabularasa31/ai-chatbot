import type { GapItem } from "@/lib/api";
import { GapRow, type RowContext } from "./GapRow";
import { EMPTY_COPY, archiveGroups, type GapShow } from "./shared";

function Rows({ items, context, Heading }: { items: GapItem[]; context: RowContext; Heading: "h2" | "h3" }) {
  return (
    <ul className="border-t border-line">
      {items.map((item) => (
        <GapRow key={item.id} item={item} context={context} Heading={Heading} />
      ))}
    </ul>
  );
}

export function GapList({ items, show, context }: { items: GapItem[]; show: GapShow; context: RowContext }) {
  if (items.length === 0) return <p className="max-w-prose text-ui text-fg-muted">{EMPTY_COPY[show]}</p>;
  if (show !== "archived") return <Rows items={items} context={context} Heading="h2" />;
  return (
    <div className="space-y-6">
      {archiveGroups(items).map((group) => (
        <section key={group.id} aria-labelledby={`gap-group-${group.id}`} className="space-y-2">
          <div>
            <h2 id={`gap-group-${group.id}`} className="text-body font-semibold text-fg">
              {group.title}
            </h2>
            {group.caption && <p className="text-caption text-fg-muted">{group.caption}</p>}
          </div>
          <Rows items={group.items} context={context} Heading="h3" />
        </section>
      ))}
    </div>
  );
}
