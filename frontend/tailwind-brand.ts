// Shared design tokens (DESIGN.md). Imported by the dashboard's
// `frontend/tailwind.config.ts` and the standalone widget-app's
// `frontend/apps/widget-app/tailwind.config.ts` so both builds stay in sync.
import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";
import { BRAND_VIOLET } from "./lib/brand";

export const ND_BRAND_COLORS = {
  base: "#0A0A0F",
  "base-alt": "#12121A",
  surface: "#1E1E2E",
  border: "#2E2E3E",
  text: "#FAF5FF",
  accent: "#E879F9",
  "accent-hover": "#f099fb",
  info: "#38BDF8",
  success: "#4ADE80",
  danger: "#F87171",
} as const;

const CHROME_COLORS = {
  chrome: "#0A0A0F",
  "chrome-fg": "#FAF5FF",
  "chrome-fg-muted": "#A9A3BA",
  "chrome-line": "#2E2E3E",
  "chrome-line-strong": "#7C7793",
  "chrome-hover": "#1E1E2E",
  "chrome-press": "#2A2A3D",
  "chrome-active": "#2A2147",
  "chrome-accent": "#A78BFA",
  "chrome-accent-fg": "#0A0A0F",
} as const;

const SCHEME_COLORS = {
  light: {
    canvas: "#F8F9FA",
    surface: "#FFFFFF",
    "surface-alt": "#F1F5F9",
    line: "#E2E8F0",
    "line-button": "#CBD5E1",
    "line-strong": "#7C8BA1",
    fg: "#1E293B",
    "fg-muted": "#475569",
    "fg-faint": "#586579",
    brand: BRAND_VIOLET,
    "brand-hover": "#6D28D9",
    "brand-active": "#5B21B6",
    "brand-on": "#FFFFFF",
    "brand-subtle": "#EDE9FE",
    focus: "#7C3AED",
    success: "#15803D",
    "success-subtle": "#F0FDF4",
    warning: "#A15C07",
    "warning-subtle": "#FFFBEB",
    danger: "#B91C1C",
    "danger-subtle": "#FEF2F2",
    "danger-hover": "#991B1B",
    "danger-active": "#7F1D1D",
    "danger-on": "#FFFFFF",
    info: "#0369A1",
    "info-subtle": "#F0F9FF",
    scrim: "#0A0A0F",
  },
  dark: {
    canvas: "#1A1A26",
    surface: "#222233",
    "surface-alt": "#2B2B3F",
    line: "#3A3A50",
    "line-button": "#4A4A63",
    "line-strong": "#7C7793",
    fg: "#FAF5FF",
    "fg-muted": "#A9A3BA",
    "fg-faint": "#9B95AD",
    brand: "#A78BFA",
    "brand-hover": "#C4B5FD",
    "brand-active": "#8B5CF6",
    "brand-on": "#0A0A0F",
    "brand-subtle": "#2D2552",
    focus: "#A78BFA",
    success: "#4ADE80",
    "success-subtle": "#1A3626",
    warning: "#FBBF24",
    "warning-subtle": "#3A2D16",
    danger: "#F87171",
    "danger-subtle": "#432227",
    "danger-hover": "#FCA5A5",
    "danger-active": "#EF4444",
    "danger-on": "#0A0A0F",
    info: "#38BDF8",
    "info-subtle": "#183246",
    scrim: "#0A0A0F",
  },
} as const;

const SCHEME_SHADOWS = {
  light: {
    pop: "0 4px 12px rgb(15 23 42 / 0.12)",
    overlay: "0 16px 40px rgb(15 23 42 / 0.16)",
  },
  dark: {
    pop: "0 4px 12px rgb(0 0 0 / 0.5)",
    overlay: "0 16px 40px rgb(0 0 0 / 0.5)",
  },
} as const;

type Scheme = keyof typeof SCHEME_COLORS;

const SCRIM_OPACITY = 0.6;
const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const SCHEME_SELECTOR: Record<Scheme, string> = { light: ":root", dark: ".dark" };

export const SYSTEM_SANS = ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"];
export const SYSTEM_MONO = ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"];

function hexToChannels(hex: string): [number, number, number] {
  const value = parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function hexToHsl(hex: string): string {
  const [r, g, b] = hexToChannels(hex).map((c) => c / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  const delta = max - min;
  if (delta === 0) return `0 0% ${round(lightness * 100)}%`;
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  const hue =
    max === r ? ((g - b) / delta) % 6 : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return `${round((hue * 60 + 360) % 360)} ${round(saturation * 100)}% ${round(lightness * 100)}%`;
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}

function schemeVariables(scheme: Scheme): Record<string, string> {
  const variables: Record<string, string> = {};
  for (const [token, hex] of Object.entries(SCHEME_COLORS[scheme])) {
    variables[`--c-${token}`] = hexToChannels(hex).join(" ");
  }
  variables["--shadow-pop"] = SCHEME_SHADOWS[scheme].pop;
  variables["--shadow-overlay"] = SCHEME_SHADOWS[scheme].overlay;
  return variables;
}

const colorTokens = [...Object.keys(SCHEME_COLORS.light), ...Object.keys(CHROME_COLORS)].filter(
  (token) => token !== "scrim",
);

const brandColors = {
  ...Object.fromEntries(
    colorTokens.map((token) => [token, `rgb(var(--c-${token}) / <alpha-value>)`]),
  ),
  scrim: `rgb(var(--c-scrim) / ${SCRIM_OPACITY})`,
};

export function brandTokensPlugin(
  schemes: readonly Scheme[],
  selectors: Partial<Record<Scheme, string>> = {},
) {
  const selectorFor = (scheme: Scheme) => selectors[scheme] ?? SCHEME_SELECTOR[scheme];
  return plugin(({ addBase, addUtilities, theme }) => {
    addBase(Object.fromEntries(schemes.map((s) => [selectorFor(s), schemeVariables(s)])));
    addUtilities({
      ".text-display": { fontSize: "1.5rem", lineHeight: "2rem", fontWeight: "600", letterSpacing: "-0.02em" },
      [`@media (min-width: ${theme("screens.md")})`]: {
        ".text-display": { fontSize: "2.25rem", lineHeight: "2.75rem" },
      },
    });
  });
}

export function chromeTokensPlugin() {
  return plugin(({ addBase }) => {
    addBase({
      ":root": Object.fromEntries(
        Object.entries(CHROME_COLORS).map(([token, hex]) => [`--c-${token}`, hexToChannels(hex).join(" ")]),
      ),
    });
  });
}

export const brandThemeExtend = {
  colors: brandColors,
  fontSize: {
    caption: ["0.75rem", { lineHeight: "1rem" }],
    ui: ["0.875rem", { lineHeight: "1.25rem" }],
    body: ["1rem", { lineHeight: "1.5rem" }],
    h2: ["1.25rem", { lineHeight: "1.75rem", fontWeight: "600", letterSpacing: "-0.01em" }],
    h1: ["1.5rem", { lineHeight: "2rem", fontWeight: "600", letterSpacing: "-0.015em" }],
  },
  transitionDuration: {
    DEFAULT: "120ms",
  },
  transitionTimingFunction: {
    DEFAULT: EASE_OUT,
  },
  scale: {
    press: "0.97",
    "press-sm": "0.94",
    "press-wide": "0.985",
  },
  maxWidth: {
    measure: "34rem",
  },
  height: {
    demo: "30rem",
    "demo-sm": "35rem",
  },
  borderRadius: {
    tag: "4px",
    control: "8px",
    surface: "12px",
    bubble: "16px",
  },
  boxShadow: {
    pop: "var(--shadow-pop)",
    overlay: "var(--shadow-overlay)",
    "chrome-selected": "inset 2px 0 0 rgb(var(--c-chrome-accent))",
  },
} satisfies NonNullable<Config["theme"]>;

function fumadocsScheme(scheme: Scheme) {
  const c = SCHEME_COLORS[scheme];
  return {
    background: hexToHsl(c.canvas),
    foreground: hexToHsl(c.fg),
    muted: hexToHsl(c["surface-alt"]),
    "muted-foreground": hexToHsl(c["fg-muted"]),
    popover: hexToHsl(c.surface),
    "popover-foreground": hexToHsl(c.fg),
    card: hexToHsl(c.surface),
    "card-foreground": hexToHsl(c.fg),
    border: hexToHsl(c.line),
    primary: hexToHsl(c.brand),
    "primary-foreground": hexToHsl(c["brand-on"]),
    secondary: hexToHsl(c["surface-alt"]),
    "secondary-foreground": hexToHsl(c.fg),
    accent: hexToHsl(c["surface-alt"]),
    "accent-foreground": hexToHsl(c.fg),
    ring: hexToHsl(c.focus),
  };
}

export const fumadocsPreset = {
  light: fumadocsScheme("light"),
  dark: fumadocsScheme("dark"),
};

const proseColors = {
  "--tw-prose-body": "theme('colors.fg')",
  "--tw-prose-headings": "theme('colors.fg')",
  "--tw-prose-lead": "theme('colors.fg-muted')",
  "--tw-prose-links": "theme('colors.brand')",
  "--tw-prose-bold": "theme('colors.fg')",
  "--tw-prose-counters": "theme('colors.fg-muted')",
  "--tw-prose-bullets": "theme('colors.fg-muted')",
  "--tw-prose-hr": "theme('colors.line')",
  "--tw-prose-quotes": "theme('colors.fg-muted')",
  "--tw-prose-quote-borders": "theme('colors.line-strong')",
  "--tw-prose-captions": "theme('colors.fg-muted')",
  "--tw-prose-code": "theme('colors.fg')",
  "--tw-prose-th-borders": "theme('colors.line')",
  "--tw-prose-td-borders": "theme('colors.line')",
  "--tw-prose-kbd": "theme('colors.fg')",
  "--tw-prose-kbd-shadows": "theme('colors.line-strong')",
};

const proseHeadings = {
  h1: {
    fontSize: "1.5rem",
    lineHeight: "2rem",
    fontWeight: "600",
    letterSpacing: "-0.015em",
    marginBottom: "1rem",
  },
  h2: {
    fontSize: "1.25rem",
    lineHeight: "1.75rem",
    fontWeight: "600",
    letterSpacing: "-0.01em",
    marginTop: "3rem",
    marginBottom: "1rem",
  },
  "h3, h4": {
    fontSize: "1rem",
    lineHeight: "1.5rem",
    fontWeight: "600",
    marginTop: "2rem",
    marginBottom: "0.5rem",
  },
  "h2 code, h3 code": { fontSize: "0.875rem" },
};

export const docsTypography = {
  DEFAULT: {
    css: [
      proseColors,
      {
        lineHeight: "1.5rem",
        "p, li, dd, blockquote, h1, h2, h3, h4": { maxWidth: brandThemeExtend.maxWidth.measure },
        p: { marginTop: "1rem", marginBottom: "1rem" },
        li: { marginTop: "0.25rem", marginBottom: "0.25rem" },
        ...proseHeadings,
        code: {
          fontFamily: ["var(--font-geist-mono)", ...SYSTEM_MONO].join(", "),
          fontSize: "0.875rem",
          padding: "0 0.25rem",
          border: "none",
          borderRadius: "theme('borderRadius.tag')",
          background: "theme('colors.surface-alt')",
        },
        "a:not([data-card])": {
          fontWeight: "400",
          textDecorationColor: "currentColor",
          transition: `color 120ms ${EASE_OUT}`,
        },
        "a:not([data-card]):hover": { color: "theme('colors.brand-hover')", opacity: "1" },
        "a:not([data-card]):focus-visible": {
          outline: "2px solid theme('colors.focus')",
          outlineOffset: "2px",
          borderRadius: "theme('borderRadius.tag')",
        },
        blockquote: {
          fontStyle: "normal",
          fontWeight: "400",
          borderInlineStartWidth: "2px",
          marginTop: "1rem",
          marginBottom: "1rem",
        },
        "blockquote p:first-of-type::before": { content: "none" },
        "blockquote p:last-of-type::after": { content: "none" },
        table: { fontSize: "0.875rem", lineHeight: "1.25rem" },
        "thead th": {
          fontSize: "0.75rem",
          lineHeight: "1rem",
          fontWeight: "500",
          color: "theme('colors.fg-muted')",
        },
        "th, td": { paddingInline: "1rem" },
      },
    ],
  },
} satisfies NonNullable<Config["theme"]>["typography"];
