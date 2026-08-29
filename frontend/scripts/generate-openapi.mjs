#!/usr/bin/env node
// Produces openapi.public.json for the docs site.
//
// The schema describes the code being built, so it is generated from that code
// by scripts/generate-openapi.py. Fetching it from a deployed instance -- which
// this script used to do, unconditionally -- meant the docs could describe a
// different commit than the one in the tree, and on 2026-08-28 it meant a
// backend outage failed this step on every branch. That turned the check suite
// red, and Railway will not deploy on a red suite, so the deploy it refused was
// the one that would have brought the API back.
//
// The network fetch survives as a last resort, for build environments that have
// Node but not the backend's Python dependencies -- Vercel's, specifically. It
// cannot recreate the deadlock: CI generates from code, so an outage no longer
// reds the suite that gates the backend deploy. It only means the frontend
// deploy waits for the API to come back.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { filterPublicSpec } from './openapi-filter.mjs';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const generator = resolve(repoRoot, 'scripts', 'generate-openapi.py');
const outputPath = resolve(repoRoot, 'frontend', 'openapi.public.json');
const sourceUrl = process.env.OPENAPI_URL ?? 'https://api.getchat9.live/openapi.json';

// A schema that exists but does not parse is worse than none: generate-docs
// clears content/docs/api before reading it, so a bad file publishes an empty
// API reference rather than failing.
function publicPathCount() {
  if (!existsSync(outputPath)) return null;
  try {
    const spec = JSON.parse(readFileSync(outputPath, 'utf8'));
    const count = Object.keys(spec.paths ?? {}).length;
    return count > 0 ? count : null;
  } catch {
    return null;
  }
}

const notes = [];

// 1. From the code in this tree. The only path that is guaranteed to describe
//    the commit being built.
const interpreters = [process.env.PYTHON, 'python3', 'python'].filter(Boolean);
for (const bin of interpreters) {
  const run = spawnSync(bin, [generator], { cwd: repoRoot, stdio: 'inherit' });
  if (run.error) {
    notes.push(`${bin}: ${run.error.message}`);
    continue;
  }
  if (run.status !== 0) {
    notes.push(`${bin}: exited ${run.status}`);
    continue;
  }
  // Trusting the exit code alone would accept an interpreter that succeeded
  // without writing anything -- PYTHON pointing at the wrong binary, say.
  if (publicPathCount() === null) {
    notes.push(`${bin}: exited 0 but wrote no usable schema`);
    continue;
  }
  process.exit(0);
}

// 2. From the deployed API. Reachable everywhere Node is, which is what makes
//    it the fallback for a build image without the backend installed.
try {
  const res = await fetch(sourceUrl);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const spec = filterPublicSpec(await res.json());
  const count = Object.keys(spec.paths).length;
  if (count === 0) throw new Error('no public paths matched');

  const tmp = `${outputPath}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(spec, null, 2)}\n`);
  renameSync(tmp, outputPath);
  console.warn(
    `[openapi] could not generate from source (${notes.join('; ')}); ` +
      `fetched ${sourceUrl} instead (${count} paths). This describes what is ` +
      `deployed, which may differ from this commit.`
  );
  process.exit(0);
} catch (err) {
  notes.push(`${sourceUrl}: ${err.message}`);
}

// 3. Whatever a previous run left behind. Stale, but a build with last week's
//    reference beats no build at all.
const existing = publicPathCount();
if (existing !== null) {
  console.warn(
    `[openapi] could not obtain a fresh schema (${notes.join('; ')}); ` +
      `building against the existing openapi.public.json (${existing} paths), ` +
      `which may be out of date.`
  );
  process.exit(0);
}

console.error(
  `[openapi] could not obtain openapi.public.json by any route:\n` +
    notes.map((n) => `  ${n}`).join('\n') +
    `\n\nThe generator imports the backend, so it needs the backend's ` +
    `dependencies:\n  pip install -r requirements.txt\n` +
    `Set PYTHON to point at the interpreter that has them, or set OPENAPI_URL ` +
    `to a reachable API.`
);
process.exit(1);
