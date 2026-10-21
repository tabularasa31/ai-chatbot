import type { GapItem } from "@/lib/api";
import { QuestionList } from "../../../../knowledge/_components/QuestionList";

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;

export function DraftContext({ cluster }: { cluster: GapItem }) {
  return (
    <aside className="space-y-4 border-line lg:border-l lg:pl-6">
      <div>
        <h2 className="text-body font-semibold text-fg">Visitor questions for this gap</h2>
        <p className="text-caption tabular-nums text-fg-muted">
          {plural(cluster.question_count, "question")} · {plural(cluster.example_questions.length, "example")} shown
        </p>
      </div>
      {cluster.example_questions.length > 0 && <QuestionList questions={cluster.example_questions} />}
      {cluster.linked_label && <p className="text-ui text-fg-muted">Docs gap: {cluster.linked_label}</p>}
    </aside>
  );
}
