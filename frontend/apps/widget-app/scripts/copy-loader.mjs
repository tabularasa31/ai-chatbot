#!/usr/bin/env node
// Copies the widget-loader IIFE bundle into widget-app's dist root so a single
// Vercel deploy serves both /widget.js (loader) and /v1/* (widget UI).
// Run from widget-app dir after `vite build` and `pnpm --filter @chat9/widget-loader build`.

import { copyFileSync, existsSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";

const SRC = "../widget-loader/dist/widget.js";
const DEST = "dist/widget.js";

if (!existsSync(SRC)) {
  console.error(`[copy-loader] source missing: ${SRC}. Did widget-loader build run?`);
  process.exit(1);
}

copyFileSync(SRC, DEST);

const raw = readFileSync(DEST);
const gz = gzipSync(raw);
const fmt = (n) => `${(n / 1024).toFixed(2)} kB`;
console.log(`[copy-loader] ${SRC} → ${DEST}  raw=${fmt(raw.length)}  gzip=${fmt(gz.length)}`);

// Hard ceiling for the loader: the plan targets 30KB gzip; anything bigger means
// we're shipping ChatWidget-class deps in the loader by accident.
const HARD = 30 * 1024;
if (gz.length > HARD) {
  console.error(`[copy-loader] FAIL: loader gzip ${fmt(gz.length)} exceeds budget ${fmt(HARD)}.`);
  process.exit(1);
}
