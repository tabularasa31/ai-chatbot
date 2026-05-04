import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  // Served from https://widget.getchat9.live/v1/. base prefixes asset URLs and
  // outDir nests the build so Vercel exposes index.html at /v1/, leaving room
  // for future /v2/ to live alongside it on the same project.
  base: "/v1/",
  resolve: {
    alias: {
      react: "preact/compat",
      "react-dom": "preact/compat",
      "react/jsx-runtime": "preact/jsx-runtime",
    },
  },
  build: {
    outDir: "dist/v1",
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
