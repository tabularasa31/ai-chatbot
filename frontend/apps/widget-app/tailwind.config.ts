import type { Config } from "tailwindcss";
import { ND_BRAND_COLORS } from "../../tailwind-brand";

// Brand palette is shared with the dashboard via `frontend/tailwind-brand.ts`
// so widget classes like `bg-nd-base-alt`, `from-nd-accent`, etc. resolve
// identically when the widget-app is built standalone via vite.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        nd: ND_BRAND_COLORS,
      },
    },
  },
  plugins: [],
};

export default config;
