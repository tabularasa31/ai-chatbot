import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";
import { ND_BRAND_COLORS, brandThemeExtend, brandTokensPlugin } from "../../tailwind-brand";

// Brand palette is shared with the dashboard via `frontend/tailwind-brand.ts`
// so widget classes like `bg-nd-base-alt`, `from-nd-accent`, etc. resolve
// identically when the widget-app is built standalone via vite.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      ...brandThemeExtend,
      colors: {
        ...brandThemeExtend.colors,
        nd: ND_BRAND_COLORS,
      },
    },
  },
  plugins: [typography, brandTokensPlugin(["light"])],
};

export default config;
