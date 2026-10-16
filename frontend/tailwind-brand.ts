// Shared design tokens (DESIGN.md). Imported by the dashboard's
// `frontend/tailwind.config.ts` and the standalone widget-app's
// `frontend/apps/widget-app/tailwind.config.ts` so both builds stay in sync.
import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";

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

const SCHEME_COLORS = {
  light: {
    canvas: "#F8F9FA",
    surface: "#FFFFFF",
    "surface-alt": "#F1F5F9",
    line: "#E2E8F0",
    "line-strong": "#7C8BA1",
    fg: "#1E293B",
    "fg-muted": "#475569",
    "fg-faint": "#586579",
    brand: "#7C3AED",
    "brand-hover": "#6D28D9",
    "brand-active": "#5B21B6",
    "brand-on": "#FFFFFF",
    "brand-subtle": "#F5F3FF",
    focus: "#7C3AED",
    success: "#15803D",
    "success-subtle": "#F0FDF4",
    warning: "#B45309",
    "warning-subtle": "#FFFBEB",
    danger: "#B91C1C",
    "danger-subtle": "#FEF2F2",
    "danger-hover": "#991B1B",
    "danger-on": "#FFFFFF",
    info: "#0369A1",
    "info-subtle": "#F0F9FF",
    scrim: "#0A0A0F",
  },
  dark: {
    canvas: "#0A0A0F",
    surface: "#12121A",
    "surface-alt": "#1E1E2E",
    line: "#2E2E3E",
    "line-strong": "#716C87",
    fg: "#FAF5FF",
    "fg-muted": "#A9A3BA",
    "fg-faint": "#8A849C",
    brand: "#A78BFA",
    "brand-hover": "#C4B5FD",
    "brand-active": "#8B5CF6",
    "brand-on": "#0A0A0F",
    "brand-subtle": "#231C3F",
    focus: "#A78BFA",
    success: "#4ADE80",
    "success-subtle": "#11271B",
    warning: "#FBBF24",
    "warning-subtle": "#2B2112",
    danger: "#F87171",
    "danger-subtle": "#2D1517",
    "danger-hover": "#FCA5A5",
    "danger-on": "#0A0A0F",
    info: "#38BDF8",
    "info-subtle": "#10222F",
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

const colorTokens = Object.keys(SCHEME_COLORS.light).filter((token) => token !== "scrim");

const brandColors = {
  ...Object.fromEntries(
    colorTokens.map((token) => [token, `rgb(var(--c-${token}) / <alpha-value>)`]),
  ),
  scrim: `rgb(var(--c-scrim) / ${SCRIM_OPACITY})`,
};

export function brandTokensPlugin(schemes: readonly Scheme[]) {
  return plugin(({ addBase, addUtilities, theme }) => {
    addBase(Object.fromEntries(schemes.map((s) => [SCHEME_SELECTOR[s], schemeVariables(s)])));
    addUtilities({
      ".text-display": { fontSize: "1.5rem", lineHeight: "2rem", fontWeight: "600" },
      [`@media (min-width: ${theme("screens.md")})`]: {
        ".text-display": { fontSize: "2.25rem", lineHeight: "2.75rem" },
      },
    });
  });
}

export const brandThemeExtend = {
  colors: brandColors,
  fontSize: {
    caption: ["0.75rem", { lineHeight: "1rem" }],
    ui: ["0.875rem", { lineHeight: "1.25rem" }],
    body: ["1rem", { lineHeight: "1.5rem" }],
    h2: ["1.25rem", { lineHeight: "1.75rem", fontWeight: "600" }],
    h1: ["1.5rem", { lineHeight: "2rem", fontWeight: "600" }],
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
    accent: hexToHsl(c["brand-subtle"]),
    "accent-foreground": hexToHsl(c.fg),
    ring: hexToHsl(c.focus),
  };
}

export const fumadocsPreset = {
  light: fumadocsScheme("light"),
  dark: fumadocsScheme("dark"),
};
