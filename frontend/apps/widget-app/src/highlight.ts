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

// Subset of highlight.js grammars, hand-registered to keep the bundle small.
// rehype-highlight can't be used: it statically imports lowlight's full `common`
// set as a fallback, so the bundler retains all ~35 grammars regardless. Each
// grammar carries its own aliases (js, ts, py, yml, sh); languages outside the
// subset render as plain monospace.
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

// Highlight fenced code blocks via the subset instance; unregistered languages
// are left untouched. Mirrors rehype-highlight's `<pre><code>` handling.
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
