import type { Element, Root } from "hast";
import { toText } from "hast-util-to-text";
import { createLowlight } from "lowlight";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";
import bash from "highlight.js/lib/languages/bash";
import go from "highlight.js/lib/languages/go";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import plaintext from "highlight.js/lib/languages/plaintext";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import yaml from "highlight.js/lib/languages/yaml";

// Explicit subset of highlight.js grammars bundled into the widget. This is
// what keeps the bundle under budget. rehype-highlight can't be used directly:
// it statically imports lowlight's `common` set (~35 grammars, ~90 kB gzip) as
// an unconditional fallback, so the bundler retains all of them even when a
// smaller `languages` option is passed. Building our own lowlight instance from
// an empty registry and registering only these grammars is the only way to
// actually drop the rest from the bundle.
//
// Each grammar registers its own highlight.js aliases (js→javascript,
// ts→typescript, py→python, yml→yaml, sh/shell→bash), so fenced blocks tagged
// with those still highlight. Blocks whose language is outside this subset
// render as plain monospace — a graceful fallback, not an error.
const lowlight = createLowlight({
  bash,
  go,
  javascript,
  json,
  markdown,
  plaintext,
  python,
  sql,
  typescript,
  yaml,
});

/** Extract the `language-x` / `lang-x` name from a `<code>` node's classes. */
function codeLanguage(node: Element): string | undefined {
  const list = node.properties?.className;
  if (!Array.isArray(list)) return undefined;
  for (const value of list) {
    const name = String(value);
    if (name === "no-highlight" || name === "nohighlight") return undefined;
    if (name.startsWith("language-")) return name.slice(9);
    if (name.startsWith("lang-")) return name.slice(5);
  }
  return undefined;
}

/**
 * Minimal rehype plugin: highlight fenced code blocks using the subset lowlight
 * instance above. Mirrors rehype-highlight's `<pre><code>` handling but without
 * its `common` import and without auto-detection. Unregistered languages fall
 * through untouched (plain monospace).
 */
export const rehypeHighlightSubset: Plugin<[], Root> = () => (tree: Root) => {
  visit(tree, "element", (node, _index, parent) => {
    if (
      node.tagName !== "code" ||
      !parent ||
      parent.type !== "element" ||
      (parent as Element).tagName !== "pre"
    ) {
      return;
    }

    const lang = codeLanguage(node);
    if (!lang || !lowlight.registered(lang)) return;

    if (!Array.isArray(node.properties.className)) node.properties.className = [];
    if (!node.properties.className.includes("hljs")) {
      node.properties.className.unshift("hljs");
    }

    const result = lowlight.highlight(lang, toText(node, { whitespace: "pre" }));
    if (result.children.length > 0) {
      node.children = result.children as Element["children"];
    }
  });
};
