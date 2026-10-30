import type { Config } from "tailwindcss";
import {
  SYSTEM_MONO,
  SYSTEM_SANS,
  brandThemeExtend,
  brandTokensPlugin,
  chromeTokensPlugin,
} from "../../tailwind-brand";

const DARK_SELECTOR = '[data-theme="dark"]';

// Both schemes come from the shared brand tokens (`frontend/tailwind-brand.ts`);
// dark is emitted under the widget's own theme attribute.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", DARK_SELECTOR],
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      ...brandThemeExtend,
      transitionDuration: { DEFAULT: "120ms" },
      transitionTimingFunction: { DEFAULT: "cubic-bezier(0.23, 1, 0.32, 1)" },
      fontFamily: { sans: SYSTEM_SANS, mono: SYSTEM_MONO },
    },
  },
  plugins: [brandTokensPlugin(["light", "dark"], { dark: `html${DARK_SELECTOR}` }), chromeTokensPlugin()],
};

export default config;
