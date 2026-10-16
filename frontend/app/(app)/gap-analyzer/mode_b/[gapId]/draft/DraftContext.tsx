import type { GapItem } from "@/lib/api";
import { QuestionList } from "../../../_components/QuestionList";

export function DraftContext({ cluster }: { cluster: GapItem }) {
  return (
    <aside className="space-y-4 border-line lg:border-l lg:pl-6">
      <h2 className="text-body font-semibold text-fg">
        Visitor questions for this gap <span className="tabular-nums">({cluster.question_count})</span>
      </h2>
      {cluster.example_questions.length > 0 && <QuestionList questions={cluster.example_questions} />}
      {cluster.linked_label && <p className="text-ui text-fg-muted">Docs gap: {cluster.linked_label}</p>}
    </aside>
  );
}
