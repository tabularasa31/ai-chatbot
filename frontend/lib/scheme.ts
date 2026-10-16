export const SCHEMES = ["system", "light", "dark"] as const;
export type Scheme = (typeof SCHEMES)[number];

export const SCHEME_CHANGE_EVENT = "chat9-scheme-change";
const SCHEME_COOKIE = "chat9-scheme";
const SCHEME_COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60;

function parseScheme(value: string | undefined): Scheme {
  return SCHEMES.find((scheme) => scheme === value) ?? "system";
}

/** Browser-only: reads `document.cookie`. */
export function readSchemeCookie(): Scheme {
  const entry = document.cookie.split("; ").find((c) => c.startsWith(`${SCHEME_COOKIE}=`));
  return parseScheme(entry?.slice(SCHEME_COOKIE.length + 1));
}

/** Re-applies the stored scheme to `<html>`; a migrated surface calls it on mount. */
export function resolveScheme(): void {
  window.dispatchEvent(new Event(SCHEME_CHANGE_EVENT));
}

/** Stores the choice and dispatches the change event so `<html>` and every switch update. */
export function writeSchemeCookie(scheme: Scheme): void {
  document.cookie = `${SCHEME_COOKIE}=${scheme}; Path=/; Max-Age=${SCHEME_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`;
  resolveScheme();
}

// Blocking head script. A surface that forces its own scheme renders [data-scheme-forced]; while it is mounted the script leaves <html> alone.
export const SCHEME_BOOT_SCRIPT = `(function(){var d=document.documentElement,q=window.matchMedia&&matchMedia("(prefers-color-scheme: dark)");function s(){try{var m=document.cookie.match(/(?:^|; )${SCHEME_COOKIE}=(\\w+)/);return m?m[1]:"system"}catch(e){return"system"}}function a(){try{if(document.querySelector("[data-scheme-forced]"))return;var v=s();d.classList.toggle("dark",v==="dark"||v!=="light"&&!!q&&q.matches)}catch(e){}}a();try{if(q)q.addEventListener?q.addEventListener("change",a):q.addListener(a);addEventListener("${SCHEME_CHANGE_EVENT}",a)}catch(e){}})()`;
