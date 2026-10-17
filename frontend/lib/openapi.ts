import { createOpenAPI } from 'fumadocs-openapi/server';
import { CODE_THEMES } from '@/lib/code-themes';

export const openapi = createOpenAPI({ shikiOptions: { themes: CODE_THEMES } });

export const { APIPage } = openapi;
