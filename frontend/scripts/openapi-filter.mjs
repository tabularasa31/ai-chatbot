// The "what is public" rule, in JavaScript.
//
// This is a second copy: scripts/generate-openapi.py holds the same rule, and
// it is the one that runs in CI and in local builds. This copy exists only for
// the network fallback below, which Vercel needs because its build image has
// Node but not the backend's Python dependencies.
//
// Two copies of a rule diverge silently, so they are compared against each
// other in CI by scripts/check-filter-parity.mjs. Change one, change both.
export const PUBLIC_TAGS = new Set([
  'widget',
  'documents',
  'chat',
  'escalations',
  'gap-analyzer',
  'knowledge',
]);

export const PUBLIC_PATHS = new Set(['/health']);

export const FALLBACK_SERVER = 'https://api.getchat9.live';

const OPERATIONS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'];

export function filterPublicSpec(spec) {
  const paths = {};
  for (const [path, item] of Object.entries(spec.paths ?? {})) {
    if (PUBLIC_PATHS.has(path)) {
      paths[path] = item;
      continue;
    }
    const keep = OPERATIONS.some((method) =>
      item[method]?.tags?.some((tag) => PUBLIC_TAGS.has(tag))
    );
    if (keep) paths[path] = item;
  }

  spec.paths = paths;
  spec.tags = (spec.tags ?? []).filter((tag) => PUBLIC_TAGS.has(tag.name));
  if (!spec.servers || spec.servers.length === 0) {
    spec.servers = [{ url: FALLBACK_SERVER }];
  }
  return spec;
}
