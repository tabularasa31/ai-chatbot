import type { Config } from "tailwindcss";
import { createPreset } from "fumadocs-ui/tailwind-plugin";
import { ND_BRAND_COLORS } from "./tailwind-brand";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./content/**/*.{md,mdx}",
    "./node_modules/fumadocs-ui/dist/**/*.js",
    "./node_modules/fumadocs-openapi/dist/**/*.{js,mjs,cjs}",
  ],
  presets: [
    (() => {
      const brand = {
        background: "240 20% 5%",
        foreground: "275 100% 97%",
        muted: "240 16% 12%",
        "muted-foreground": "275 40% 75%",
        popover: "240 18% 8%",
        "popover-foreground": "275 100% 97%",
        card: "240 17% 9%",
        "card-foreground": "275 100% 97%",
        border: "240 15% 18%",
        primary: "291 90% 72%",
        "primary-foreground": "240 20% 5%",
        secondary: "240 15% 14%",
        "secondary-foreground": "275 100% 97%",
        accent: "189 94% 69%",
        "accent-foreground": "240 20% 5%",
        ring: "291 90% 72%",
      };
      return createPreset({
        addGlobalColors: false,
        preset: { light: brand, dark: brand },
      });
    })(),
  ],
  theme: {
    extend: {
      colors: {
        nd: ND_BRAND_COLORS,
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
export default config;
