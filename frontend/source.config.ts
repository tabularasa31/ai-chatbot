import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { CODE_THEMES } from './lib/code-themes';

export const { docs, meta } = defineDocs({
  dir: 'content/docs',
});

export default defineConfig({
  mdxOptions: { rehypeCodeOptions: { themes: CODE_THEMES } },
});
