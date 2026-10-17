import { useId, type InputHTMLAttributes } from "react";
import { cn } from "./utils";

const ROW_CLASS =
  "flex min-h-11 cursor-pointer items-start gap-3 rounded-control px-3 py-2.5 hover:bg-surface-alt " +
  "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-canvas";

const CONTROL_CLASS = "mt-0.5 h-5 w-5 shrink-0 cursor-pointer accent-brand focus:outline-none";

export function Checkbox({
  label,
  helper,
  className,
  ...props
}: { label: string; helper?: string } & Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const helperId = useId();
  return (
    <label className={cn(ROW_CLASS, className)}>
      <input
        type="checkbox"
        aria-describedby={helper ? helperId : undefined}
        className={CONTROL_CLASS}
        {...props}
      />
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
    <div role="radiogroup" aria-label={label} className="space-y-1">
      {options.map((option) => (
        <label key={option.value} className={cn(ROW_CLASS, option.value === value && "bg-brand-subtle hover:bg-brand-subtle")}>
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={option.value === value}
            onChange={() => onChange(option.value)}
            className={CONTROL_CLASS}
          />
          <span className="min-w-0">
            <span className="block text-ui font-medium text-fg">{option.label}</span>
            <span className="block text-caption text-fg-muted">{option.description}</span>
          </span>
        </label>
      ))}
    </div>
  );
}
