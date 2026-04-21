import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
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
