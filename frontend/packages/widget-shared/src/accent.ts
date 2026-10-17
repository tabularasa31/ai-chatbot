export type Rgb = readonly [number, number, number];

const ON_LIGHT: Rgb = [255, 255, 255];
const ON_DARK: Rgb = [0, 0, 0];
export const ACCENT_ON_LIGHT = "#FFFFFF";

const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const LUMINANCE_OFFSET = 0.05;

function channelLuminance(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance([r, g, b]: Rgb): number {
  return 0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + LUMINANCE_OFFSET) / (lo + LUMINANCE_OFFSET);
}

/** White or black, whichever contrasts more with the fill (at least 4.58 for any fill). */
export function accentOnRgb(fill: Rgb): Rgb {
  return contrast(fill, ON_LIGHT) >= contrast(fill, ON_DARK) ? ON_LIGHT : ON_DARK;
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
