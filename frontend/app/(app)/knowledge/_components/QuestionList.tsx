import { pluralize } from "@/lib/format";

const VISIBLE_QUESTIONS = 3;

/** First three questions stay visible; the rest sit behind a native disclosure. */
export function QuestionList({ questions }: { questions: string[] }) {
  const rest = questions.slice(VISIBLE_QUESTIONS);
  return (
    <div className="text-ui text-fg">
      <ul className="list-disc space-y-1 pl-5">
        {questions.slice(0, VISIBLE_QUESTIONS).map((question, index) => (
          <li key={`${index}:${question}`} className="break-anywhere">
            {question}
          </li>
        ))}
      </ul>
      {rest.length > 0 && (
        <details>
          <summary className="flex min-h-11 cursor-pointer items-center text-ui text-brand">
            Show {rest.length} more {pluralize(rest.length, "question", "questions")}
          </summary>
          <ul className="list-disc space-y-1 pl-5">
            {rest.map((question, index) => (
              <li key={`${index}:${question}`} className="break-anywhere">
                {question}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
