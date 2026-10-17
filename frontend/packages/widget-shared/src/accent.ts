export type Rgb = readonly [number, number, number];

export const ACCENT_ON_LIGHT = "#FFFFFF";
export const ACCENT_ON_DARK = "#0A0A0F";

const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const DARK_ON_LUMINANCE_OFFSET = 0.0532;

function channelLuminance(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** White or near-black, whichever contrasts more with the given fill. */
export function accentOnColor([r, g, b]: Rgb): string {
  const luminance =
    0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
  const contrastWithWhite = 1.05 / (luminance + 0.05);
  const contrastWithDark = (luminance + 0.05) / DARK_ON_LUMINANCE_OFFSET;
  return contrastWithWhite >= contrastWithDark ? ACCENT_ON_LIGHT : ACCENT_ON_DARK;
}

export function rgbToHex([r, g, b]: Rgb): string {
  return "#" + [r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("").toUpperCase();
}

/** Parses `#RRGGBB`; anything else is rejected so a bad URL param is ignored. */
export function parseHexColor(value: string | null | undefined): Rgb | null {
  if (!value || !HEX_COLOR.test(value)) return null;
  const n = parseInt(value.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
