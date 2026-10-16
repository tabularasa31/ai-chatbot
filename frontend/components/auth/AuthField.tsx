import type { InputHTMLAttributes } from "react";
import { cn } from "@/components/ui/utils";

export const EMAIL_INPUT_PROPS = {
  type: "email",
  inputMode: "email",
  autoCapitalize: "none",
  spellCheck: false,
  placeholder: "you@example.com",
} as const;

type AuthFieldProps = {
  id: string;
  label: string;
  helper?: string;
  error?: string | null;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "aria-invalid" | "aria-describedby">;

export function AuthField({ id, label, helper, error, className, ...input }: AuthFieldProps) {
  const describedBy = error ? `${id}-error` : helper ? `${id}-helper` : undefined;
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-ui font-medium text-fg">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          "h-11 w-full rounded-control border bg-surface px-3 text-body text-fg placeholder:text-fg-faint",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
          error ? "border-danger" : "border-line-strong",
          className,
        )}
        {...input}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-ui text-danger">
          {error}
        </p>
      ) : (
        helper && (
          <p id={`${id}-helper`} className="mt-1 text-caption text-fg-muted">
            {helper}
          </p>
        )
      )}
    </div>
  );
}
