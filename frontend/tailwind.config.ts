import type { Config } from "tailwindcss";
import { createPreset } from "fumadocs-ui/tailwind-plugin";
import {
  ND_BRAND_COLORS,
  SYSTEM_MONO,
  SYSTEM_SANS,
  brandThemeExtend,
  brandTokensPlugin,
  chromeTokensPlugin,
  fumadocsPreset,
} from "./tailwind-brand";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./content/**/*.{md,mdx}",
    "./node_modules/fumadocs-ui/dist/**/*.js",
    "./node_modules/fumadocs-openapi/dist/**/*.{js,mjs,cjs}",
  ],
  presets: [
    createPreset({
      addGlobalColors: false,
      preset: fumadocsPreset,
    }),
  ],
  theme: {
    extend: {
      ...brandThemeExtend,
      fontFamily: {
        sans: ["var(--font-geist-sans)", ...SYSTEM_SANS],
        mono: ["var(--font-geist-mono)", ...SYSTEM_MONO],
      },
      colors: {
        ...brandThemeExtend.colors,
        nd: ND_BRAND_COLORS,
      },
    },
  },
  plugins: [require("@tailwindcss/typography"), brandTokensPlugin(["light", "dark"]), chromeTokensPlugin()],
};
export default config;
