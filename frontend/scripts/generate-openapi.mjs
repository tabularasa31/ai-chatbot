#!/usr/bin/env node
// Produces openapi.public.json for the docs site.
//
// This used to fetch the schema from https://api.getchat9.live at build time,
// which was wrong twice over. The schema describes the code being built, so
// taking it from a deployed instance could document a different commit than the
// one in the working tree. And on 2026-08-28 a backend outage failed this step
// on every branch, which turned the check suite red -- Railway will not deploy
// on a red suite, so the deploy it refused was the one that would have brought
// the API back.
//
// The schema now comes from the application code in this repository, via
// scripts/generate-openapi.py. That script owns the filtering; keeping a second
// copy of it here would mean two definitions of "public" that have to agree.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const generator = resolve(repoRoot, 'scripts', 'generate-openapi.py');
const outputPath = resolve(repoRoot, 'frontend', 'openapi.public.json');

const interpreters = process.env.PYTHON ? [process.env.PYTHON] : ['python3', 'python'];

const failures = [];
for (const bin of interpreters) {
  const run = spawnSync(bin, [generator], { cwd: repoRoot, stdio: 'inherit' });
  if (run.status === 0) process.exit(0);
  failures.push(`${bin}: ${run.error ? run.error.message : `exited ${run.status}`}`);
}

// A frontend-only checkout may have no working backend environment. If a
// previous run left a schema behind, build against it rather than blocking on
// something this developer may have no reason to install.
if (existsSync(outputPath)) {
  console.warn(
    `[openapi] could not regenerate the schema (${failures.join('; ')}); ` +
      `building against the existing openapi.public.json`
  );
  process.exit(0);
}

console.error(
  `[openapi] could not generate openapi.public.json and no previous copy exists.\n` +
    failures.map((f) => `  ${f}`).join('\n') +
    `\n\nThe generator imports the backend, so it needs the backend's dependencies:\n` +
    `  pip install -r requirements.txt\n` +
    `Then rerun the build, or set PYTHON to the interpreter that has them.`
);
process.exit(1);
