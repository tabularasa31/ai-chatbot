import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: {
      react: "preact/compat",
      "react-dom": "preact/compat",
      "react/jsx-runtime": "preact/jsx-runtime",
    },
  },
  // The spike copies ChatWidget.tsx verbatim from the dashboard, where
  // `process.env.NEXT_PUBLIC_APP_URL` is replaced at build time by Next.js.
  // Vite has no equivalent, so without this `define`, `process` is undefined
  // at runtime and ChatWidget throws ReferenceError on mount. Replacing it
  // with an empty object lets the existing `|| fallback` produce the default
  // URL — keeps the spike runnable without editing the dashboard-mirrored
  // file. PR 2 will rewrite the file to read config from the loader handshake
  // and this shim goes away.
  define: {
    "process.env": "{}",
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2020",
    reportCompressedSize: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
