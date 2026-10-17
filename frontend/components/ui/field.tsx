import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./utils";

const CONTROL_CLASS =
  "w-full rounded-control border bg-surface px-3 text-ui text-fg placeholder:text-fg-faint " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface " +
  "disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-fg-faint";

const borderClass = (invalid: boolean) => (invalid ? "border-danger" : "border-line-strong");

type FieldProps = {
  label: string;
  hideLabel?: boolean;
  helper?: string;
  error?: string;
};

type Control = { id: string; describedBy?: string; invalid: boolean };

function Field({
  label,
  hideLabel,
  helper,
  error,
  className,
  children,
}: FieldProps & { className?: string; children: (control: Control) => ReactNode }) {
  const id = useId();
  const hintId = `${id}-hint`;
  const hint = error ?? helper;
  return (
    <div className={className}>
      <label htmlFor={id} className={hideLabel ? "sr-only" : "mb-1 block text-ui font-medium text-fg"}>
        {label}
      </label>
      {children({ id, describedBy: hint ? hintId : undefined, invalid: Boolean(error) })}
      {hint && (
        <p id={hintId} className={cn("mt-1 text-caption", error ? "text-danger" : "text-fg-muted")}>
          {hint}
        </p>
      )}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, FieldProps & InputHTMLAttributes<HTMLInputElement>>(function Input(
  { label, hideLabel, helper, error, className, ...props },
  ref,
) {
  return (
    <Field label={label} hideLabel={hideLabel} helper={helper} error={error} className={className}>
      {({ id, describedBy, invalid }) => (
        <input
          ref={ref}
          id={id}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={cn(CONTROL_CLASS, "h-11", borderClass(invalid))}
          {...props}
        />
      )}
    </Field>
  );
});

export function Select({
  label,
  hideLabel,
  helper,
  error,
  className,
  children,
  ...props
}: FieldProps & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <Field label={label} hideLabel={hideLabel} helper={helper} error={error} className={className}>
      {({ id, describedBy, invalid }) => (
        <div className="relative">
          <select
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            className={cn(CONTROL_CLASS, "h-11 appearance-none pr-10", borderClass(invalid))}
            {...props}
          >
            {children}
          </select>
          <ChevronDown
            size={16}
            aria-hidden="true"
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-fg-muted"
          />
        </div>
      )}
    </Field>
  );
}

export function Textarea({
  label,
  hideLabel,
  helper,
  error,
  mono,
  className,
  ...props
}: FieldProps & { mono?: boolean } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <Field label={label} hideLabel={hideLabel} helper={helper} error={error} className={className}>
      {({ id, describedBy, invalid }) => (
        <textarea
          id={id}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={cn(CONTROL_CLASS, "min-h-11 py-2", mono && "font-mono", borderClass(invalid))}
          {...props}
        />
      )}
    </Field>
  );
}
