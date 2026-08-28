#!/usr/bin/env node
import { writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const SOURCE_URL =
  process.env.OPENAPI_URL ?? 'https://api.getchat9.live/openapi.json';
const OUTPUT_PATH = resolve(process.cwd(), 'openapi.public.json');

const PUBLIC_TAGS = new Set([
  'widget',
  'documents',
  'chat',
  'escalations',
  'gap-analyzer',
  'knowledge',
]);

const PUBLIC_PATHS = new Set(['/health']);

// The live API is a convenience for refreshing this file, never a
// precondition for building. It used to be one, and on 2026-08-28 that turned
// a single backend outage into a deadlock: the frontend build failed on every
// branch, the check suite went red, Railway refuses to deploy on a red suite,
// and the deploy it refused was the one that would have brought the API back.
//
// So a failed fetch now falls back to the committed openapi.public.json, and
// the build carries on with a schema that may be a few commits stale rather
// than no schema at all.
let spec;
try {
  const res = await fetch(SOURCE_URL);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  spec = await res.json();
} catch (err) {
  if (!existsSync(OUTPUT_PATH)) {
    throw new Error(
      `Failed to fetch ${SOURCE_URL} (${err.message}) and ${OUTPUT_PATH} is not ` +
        `checked in, so there is nothing to fall back to. Regenerate it with ` +
        `scripts/generate-openapi.py and commit the result.`
    );
  }
  console.warn(
    `[fetch-openapi] ${SOURCE_URL} unreachable (${err.message}); building ` +
      `against the committed schema instead.`
  );
  process.exit(0);
}

const operationKeys = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'];

const filteredPaths = {};
for (const [path, item] of Object.entries(spec.paths ?? {})) {
  if (PUBLIC_PATHS.has(path)) {
    filteredPaths[path] = item;
    continue;
  }
  const keep = operationKeys.some((m) => {
    const op = item[m];
    return op?.tags?.some((t) => PUBLIC_TAGS.has(t));
  });
  if (keep) filteredPaths[path] = item;
}

spec.paths = filteredPaths;
spec.tags = (spec.tags ?? []).filter((t) => PUBLIC_TAGS.has(t.name));

if (!spec.servers || spec.servers.length === 0) {
  spec.servers = [{ url: 'https://api.getchat9.live' }];
}

await writeFile(OUTPUT_PATH, JSON.stringify(spec, null, 2));

const count = Object.keys(filteredPaths).length;
console.log(`[fetch-openapi] wrote ${OUTPUT_PATH} (${count} paths)`);
