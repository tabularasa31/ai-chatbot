#!/usr/bin/env node
// Asserts that the two copies of the "what is public" rule agree.
//
// scripts/generate-openapi.py decides it for every ordinary build;
// scripts/openapi-filter.mjs decides it for the network fallback that Vercel
// uses. Nothing forces them to stay in step, and a divergence would be silent:
// the published API reference would depend on which route happened to run.
//
// So run both over the same input and compare.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { filterPublicSpec } from './openapi-filter.mjs';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const generator = resolve(repoRoot, 'scripts', 'generate-openapi.py');
const scratch = mkdtempSync(join(tmpdir(), 'openapi-parity-'));
const rawPath = join(scratch, 'raw.json');

try {
  const python = process.env.PYTHON ?? 'python3';
  const run = spawnSync(python, [generator, '--raw', rawPath], {
    cwd: repoRoot,
    stdio: 'inherit',
  });
  if (run.status !== 0) {
    console.error('[parity] could not run the Python generator');
    process.exit(1);
  }

  const fromPython = JSON.parse(
    readFileSync(resolve(repoRoot, 'frontend', 'openapi.public.json'), 'utf8')
  );
  const fromNode = filterPublicSpec(JSON.parse(readFileSync(rawPath, 'utf8')));

  const pythonPaths = Object.keys(fromPython.paths).sort();
  const nodePaths = Object.keys(fromNode.paths).sort();
  const onlyPython = pythonPaths.filter((p) => !nodePaths.includes(p));
  const onlyNode = nodePaths.filter((p) => !pythonPaths.includes(p));

  if (onlyPython.length || onlyNode.length) {
    console.error('[parity] the two public-schema filters disagree.');
    if (onlyPython.length) console.error(`  only generate-openapi.py: ${onlyPython.join(', ')}`);
    if (onlyNode.length) console.error(`  only openapi-filter.mjs:   ${onlyNode.join(', ')}`);
    console.error(
      '\nBoth decide what the published API reference contains. Update the ' +
        'other one to match.'
    );
    process.exit(1);
  }

  const pythonTags = (fromPython.tags ?? []).map((t) => t.name).sort();
  const nodeTags = (fromNode.tags ?? []).map((t) => t.name).sort();
  if (pythonTags.join() !== nodeTags.join()) {
    console.error(
      `[parity] tag sets disagree: python ${pythonTags.join(', ')} vs ` +
        `node ${nodeTags.join(', ')}`
    );
    process.exit(1);
  }

  console.log(`[parity] both filters agree (${pythonPaths.length} paths, ${pythonTags.length} tags)`);
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
