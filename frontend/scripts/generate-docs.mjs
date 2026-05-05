#!/usr/bin/env node
import { generateFiles } from 'fumadocs-openapi';
import { rimraf } from 'rimraf';
import { resolve } from 'node:path';

const OUTPUT_DIR = resolve(process.cwd(), 'content/docs/api');

await rimraf(OUTPUT_DIR, {
  filter: (path) => !path.endsWith('index.mdx') && !path.endsWith('meta.json'),
});

await generateFiles({
  input: ['./openapi.public.json'],
  output: './content/docs/api',
  per: 'operation',
  groupBy: 'tag',
  imports: [{ names: ['APIPage'], from: '@/lib/openapi' }],
});

console.log('[generate-docs] generated MDX into', OUTPUT_DIR);
