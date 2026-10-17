import type { Config } from "tailwindcss";
import {
  SYSTEM_MONO,
  SYSTEM_SANS,
  brandThemeExtend,
  brandTokensPlugin,
  chromeTokensPlugin,
} from "../../tailwind-brand";

// Light values come from the shared brand tokens (`frontend/tailwind-brand.ts`);
// the widget's dark values live in styles.css under [data-theme="dark"].
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      ...brandThemeExtend,
      fontFamily: { sans: SYSTEM_SANS, mono: SYSTEM_MONO },
    },
  },
  plugins: [brandTokensPlugin(["light"]), chromeTokensPlugin()],
};

export default config;
