import type { Config } from "tailwindcss";
import {
  SYSTEM_MONO,
  SYSTEM_SANS,
  brandThemeExtend,
  brandTokensPlugin,
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
      colors: {
        ...brandThemeExtend.colors,
        chrome: {
          DEFAULT: "#0A0A0F",
          fg: "#FAF5FF",
          "fg-muted": "#A9A3BA",
          hover: "#1E1E2E",
        },
      },
    },
  },
  plugins: [brandTokensPlugin(["light"])],
};

export default config;
