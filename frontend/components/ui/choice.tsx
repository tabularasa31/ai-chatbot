import { useId, type InputHTMLAttributes } from "react";
import { cn } from "./utils";

const ROW_CLASS =
  "flex min-h-11 cursor-pointer items-start gap-3 rounded-control px-3 py-2.5 hover:bg-surface-alt " +
  "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-canvas";

const CONTROL_CLASS = "mt-0.5 h-5 w-5 shrink-0 cursor-pointer accent-brand focus:outline-none";

type ChoiceInputProps = { type: "checkbox" | "radio" } & Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

function ChoiceRow({
  label,
  helper,
  selected,
  className,
  ...input
}: { label: string; helper?: string; selected?: boolean; className?: string } & ChoiceInputProps) {
  const helperId = useId();
  return (
    <label className={cn(ROW_CLASS, selected && "bg-brand-subtle hover:bg-brand-subtle", className)}>
      <input aria-describedby={helper ? helperId : undefined} className={CONTROL_CLASS} {...input} />
      <span className="min-w-0">
        <span className="block text-ui font-medium text-fg">{label}</span>
        {helper && (
          <span id={helperId} className="block text-caption text-fg-muted">
            {helper}
          </span>
        )}
      </span>
    </label>
  );
}

export function Checkbox(props: { label: string; helper?: string } & Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  return <ChoiceRow type="checkbox" {...props} />;
}

export type RadioOption<T extends string> = { value: T; label: string; description: string };

export function RadioGroup<T extends string>({
  label,
  name,
  options,
  value,
  onChange,
}: {
  label: string;
  name: string;
  options: readonly RadioOption<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="min-w-0 space-y-1">
      <legend className="sr-only">{label}</legend>
      {options.map(({ value: optionValue, label: optionLabel, description }) => (
        <ChoiceRow
          key={optionValue}
          type="radio"
          name={name}
          value={optionValue}
          label={optionLabel}
          helper={description}
          selected={optionValue === value}
          checked={optionValue === value}
          onChange={() => onChange(optionValue)}
        />
      ))}
    </fieldset>
  );
}
