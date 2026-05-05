#!/usr/bin/env node
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const SOURCE_URL =
  process.env.OPENAPI_URL ?? 'https://api.getchat9.live/openapi.json';
const OUTPUT_PATH = resolve(process.cwd(), 'openapi.public.json');

const PUBLIC_TAGS = new Set([
  'widget',
  'auth',
  'tenants',
  'bots',
  'documents',
  'chat',
  'escalations',
  'gap-analyzer',
  'knowledge',
]);

const PUBLIC_PATHS = new Set(['/health']);

const res = await fetch(SOURCE_URL);
if (!res.ok) {
  throw new Error(`Failed to fetch ${SOURCE_URL}: ${res.status} ${res.statusText}`);
}
const spec = await res.json();

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
