// Shared brand palette. Imported by both the dashboard's
// `frontend/tailwind.config.ts` and the standalone widget-app's
// `frontend/apps/widget-app/tailwind.config.ts` so the `nd-*` color tokens
// stay in sync. Update once, both configs pick it up.
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
