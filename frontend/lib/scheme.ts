export const SCHEMES = ["system", "light", "dark"] as const;
export type Scheme = (typeof SCHEMES)[number];

export const SCHEME_COOKIE = "chat9-scheme";
export const SCHEME_CHANGE_EVENT = "chat9-scheme-change";
const SCHEME_COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60;

export function parseScheme(value: string | undefined): Scheme {
  return SCHEMES.find((scheme) => scheme === value) ?? "system";
}

export function readSchemeCookie(): Scheme {
  const entry = document.cookie.split("; ").find((c) => c.startsWith(`${SCHEME_COOKIE}=`));
  return parseScheme(entry?.slice(SCHEME_COOKIE.length + 1));
}

export function writeSchemeCookie(scheme: Scheme): void {
  document.cookie = `${SCHEME_COOKIE}=${scheme}; Path=/; Max-Age=${SCHEME_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`;
  window.dispatchEvent(new Event(SCHEME_CHANGE_EVENT));
}

// Blocking head script: re-reads the cookie on every run so a stale preference never wins.
export const SCHEME_BOOT_SCRIPT = `(function(){var d=document.documentElement,q=matchMedia("(prefers-color-scheme: dark)");function a(){var m=document.cookie.match(/(?:^|; )${SCHEME_COOKIE}=(\\w+)/),s=m?m[1]:"system";d.classList.toggle("dark",s==="dark"||s!=="light"&&q.matches)}a();q.addEventListener("change",a);addEventListener("${SCHEME_CHANGE_EVENT}",a)})()`;
