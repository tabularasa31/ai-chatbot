#!/usr/bin/env node
// Asserts that the widget-app's gzipped JS bundle stays within budget.
// Run after `vite build`. Reads dist/assets/*.js, gzips, sums.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const DIST = "dist/assets";
const BUDGET_GZIP_BYTES = 150 * 1024; // 150 KB hard ceiling per PR 2 plan
const WARN_GZIP_BYTES = 130 * 1024; // ~7% headroom over current 123 KB

function listJs(dir) {
  return readdirSync(dir)
    .filter((f) => f.endsWith(".js"))
    .map((f) => join(dir, f));
}

const files = listJs(DIST);
if (files.length === 0) {
  console.error(`[bundle-budget] No .js files in ${DIST}. Did vite build run?`);
  process.exit(2);
}

let totalRaw = 0;
let totalGzip = 0;
const rows = [];
for (const f of files) {
  const raw = readFileSync(f);
  const gz = gzipSync(raw);
  totalRaw += raw.length;
  totalGzip += gz.length;
  rows.push({ file: f.replace("dist/", ""), raw: raw.length, gzip: gz.length });
}

function fmt(n) {
  return `${(n / 1024).toFixed(2)} kB`;
}

console.log("[bundle-budget] widget-app JS bundle");
for (const r of rows) {
  console.log(`  ${r.file}  raw=${fmt(r.raw)}  gzip=${fmt(r.gzip)}`);
}
console.log(`  ────`);
console.log(`  total            raw=${fmt(totalRaw)}  gzip=${fmt(totalGzip)}`);
console.log(`  budget (gzip):   warn=${fmt(WARN_GZIP_BYTES)}  hard=${fmt(BUDGET_GZIP_BYTES)}`);

if (totalGzip > BUDGET_GZIP_BYTES) {
  console.error(`[bundle-budget] FAIL: gzipped JS (${fmt(totalGzip)}) exceeds hard budget (${fmt(BUDGET_GZIP_BYTES)}).`);
  process.exit(1);
}
if (totalGzip > WARN_GZIP_BYTES) {
  console.warn(`[bundle-budget] WARN: gzipped JS (${fmt(totalGzip)}) above warning threshold (${fmt(WARN_GZIP_BYTES)}).`);
}

console.log(`[bundle-budget] OK`);
