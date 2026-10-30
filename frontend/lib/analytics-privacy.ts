import type { BeforeSendFn } from "posthog-js";

// Email links carry single-use auth tokens in the query string
// (/verify, /reset-password, /accept-invite). The `%3F`/`%26`/`%3D` forms
// catch the same URL nested, encoded, inside another URL.
export const SECRET_QUERY_PARAMS = ["token"];
const SECRET_PARAM_VALUE = new RegExp(
  `((?:[?&]|%3F|%26)(?:${SECRET_QUERY_PARAMS.join("|")})(?:=|%3D))(?:(?!%26)[^&#\\s"'])*`,
  "gi",
);
// Same marker PostHog writes for `custom_personal_data_properties`.
const REDACTED = "<masked>";

export function redactUrlSecrets(text: string): string {
  return text.replace(SECRET_PARAM_VALUE, `$1${REDACTED}`);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function redactDeep(value: unknown): unknown {
  if (typeof value === "string") return redactUrlSecrets(value);
  if (Array.isArray(value)) return value.map(redactDeep);
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, inner]) => [redactUrlSecrets(key), redactDeep(inner)]),
    );
  }
  return value;
}

// Walks keys too: heatmap events key their data by page URL.
export const redactPostHogEvent: BeforeSendFn = (event) =>
  event && (redactDeep(event) as typeof event);

export function redactInsightsEvent<T extends { url: string }>(event: T): T {
  return { ...event, url: redactUrlSecrets(event.url) };
}
