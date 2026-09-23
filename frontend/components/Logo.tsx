export function LogoMark({
  size = 32,
  tile = true,
  className,
}: {
  size?: number;
  tile?: boolean;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={tile ? "0 0 32 32" : "5 5 22 22"}
      aria-hidden="true"
      className={className}
    >
      {tile && <rect width="32" height="32" rx="8" fill="#0A0A0F" />}
      <path
        d="M5 8a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-7l-4 3v-3a3 3 0 0 1-3-3z"
        fill="#E879F9"
      />
      <path
        d="M13 20h6a4 4 0 0 0 4-4v-4h1a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3v3l-4-3h-4a3 3 0 0 1-3-3z"
        fill="#38BDF8"
      />
    </svg>
  );
}

export function Logo({
  size = 28,
  textClassName = "text-2xl",
  className = "",
}: {
  size?: number;
  textClassName?: string;
  className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-2 text-nd-text font-semibold tracking-tight ${className}`}>
      <LogoMark size={size} tile={false} />
      <span className={`${textClassName} leading-none`}>Chat9</span>
    </span>
  );
}
