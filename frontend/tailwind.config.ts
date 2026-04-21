import type { Config } from "tailwindcss";
import { createPreset } from "fumadocs-ui/tailwind-plugin";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./content/**/*.{md,mdx}",
    "./node_modules/fumadocs-ui/dist/**/*.js",
  ],
  presets: [
    createPreset({
      addGlobalColors: false,
      preset: {
        light: {
          background: "260 40% 97%",
          foreground: "260 30% 10%",
          muted: "260 20% 92%",
          "muted-foreground": "260 15% 40%",
          popover: "260 40% 99%",
          "popover-foreground": "260 30% 10%",
          card: "260 40% 98%",
          "card-foreground": "260 30% 10%",
          border: "260 20% 88%",
          primary: "291 90% 72%",
          "primary-foreground": "240 20% 5%",
          secondary: "260 20% 94%",
          "secondary-foreground": "260 30% 15%",
          accent: "189 94% 69%",
          "accent-foreground": "240 20% 5%",
          ring: "291 90% 72%",
        },
        dark: {
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
        },
      },
    }),
  ],
  theme: {
    extend: {
      colors: {
        nd: {
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
        },
      },
    },
  },
  plugins: [],
};
export default config;
