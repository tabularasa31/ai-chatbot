import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./utils";

const CONTROL_CLASS =
  "w-full rounded-control border bg-surface px-3 text-ui text-fg placeholder:text-fg-faint " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface " +
  "disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-fg-faint";

type FieldProps = {
  label: string;
  hideLabel?: boolean;
  helper?: string;
};

type Control = { id: string; describedBy?: string };

function Field({
  label,
  hideLabel,
  helper,
  className,
  children,
}: FieldProps & { className?: string; children: (control: Control) => ReactNode }) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className={className}>
      <label htmlFor={id} className={hideLabel ? "sr-only" : "mb-1 block text-ui font-medium text-fg"}>
        {label}
      </label>
      {children({ id, describedBy: helper ? hintId : undefined })}
      {helper && (
        <p id={hintId} className="mt-1 text-caption text-fg-muted">
          {helper}
        </p>
      )}
    </div>
  );
}

export function Input({
  label,
  hideLabel,
  helper,
  className,
  ...props
}: FieldProps & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <Field label={label} hideLabel={hideLabel} helper={helper} className={className}>
      {({ id, describedBy }) => (
        <input
          id={id}
          aria-describedby={describedBy}
          className={cn(CONTROL_CLASS, "h-11", "border-line-strong")}
          {...props}
        />
      )}
    </Field>
  );
}

export function Select({
  label,
  hideLabel,
  helper,
  className,
  children,
  ...props
}: FieldProps & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <Field label={label} hideLabel={hideLabel} helper={helper} className={className}>
      {({ id, describedBy }) => (
        <div className="relative">
          <select
            id={id}
            aria-describedby={describedBy}
              className={cn(CONTROL_CLASS, "h-11 appearance-none pr-10", "border-line-strong")}
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
  className,
  ...props
}: FieldProps & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <Field label={label} hideLabel={hideLabel} helper={helper} className={className}>
      {({ id, describedBy }) => (
        <textarea
          id={id}
          aria-describedby={describedBy}
          className={cn(CONTROL_CLASS, "min-h-11 py-2", "border-line-strong")}
          {...props}
        />
      )}
    </Field>
  );
}
