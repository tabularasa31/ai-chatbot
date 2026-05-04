import { defineConfig } from "vite";

// Builds the loader as a single IIFE that tenants drop on their site via a
// <script src=".../widget.js"> tag. No globals exported (extend: false +
// `name` placeholder satisfied by rollup but unused at runtime).
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2018",
    minify: "esbuild",
    sourcemap: false,
    reportCompressedSize: true,
    lib: {
      entry: "src/index.ts",
      formats: ["iife"],
      name: "Chat9Loader",
      fileName: () => "widget.js",
    },
    rollupOptions: {
      output: {
        extend: false,
      },
    },
  },
});
