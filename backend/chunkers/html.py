"""HTML main-content extraction shared by uploads and the URL crawler.

Readability-style cleanup: boilerplate tags (nav, footer, aside, forms,
scripts, ...) are stripped and the content root is ``<main>``/``<article>``
when present. Uploaded HTML is rendered to markdown-ish text (headings as
ATX ``#`` lines) so the heading-aware markdown chunker handles it; the URL
crawler reuses ``clean_html_root`` for its own sectioning.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

# Boilerplate that must never reach retrieval chunks.
STRIPPED_TAGS = (
    "script",
    "style",
    "nav",
    "footer",
    "aside",
    "noscript",
    "form",
    "iframe",
    "svg",
    "button",
    "template",
)

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_CONTENT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "table", "blockquote"]


def clean_html_root(html: str):
    """Parse HTML, strip boilerplate, return (soup, content_root)."""
    soup = BeautifulSoup(html, "html.parser")
    for selector in STRIPPED_TAGS:
        for node in soup.select(selector):
            node.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    return soup, root


def _render_table(node) -> str:
    rows: list[str] = []
    for tr in node.find_all("tr"):
        cells = [
            " ".join(cell.get_text(" ", strip=True).split())
            for cell in tr.find_all(["th", "td"])
        ]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    if len(rows) < 2:
        return "\n".join(rows)
    # Header separator after the first row so downstream chunkers and the
    # LLM read it as a markdown table.
    column_count = rows[0].count("|") - 1
    separator = "|" + " --- |" * max(column_count, 1)
    return "\n".join([rows[0], separator, *rows[1:]])


def html_to_markdown_text(html: str) -> str:
    """Extract main content as markdown-ish text (ATX headings preserved)."""
    _, root = clean_html_root(html)

    blocks: list[str] = []
    seen: set[int] = set()
    for node in root.find_all(_CONTENT_TAGS, recursive=True):
        # Skip nodes nested inside an already-captured block (e.g. <p> inside
        # <li>, <li> inside <table>) to avoid duplicating their text.
        if any(id(parent) in seen for parent in node.parents):
            continue
        name = node.name.lower()
        if name == "table":
            rendered = _render_table(node)
            if rendered:
                blocks.append(rendered)
                seen.add(id(node))
            continue
        text = node.get_text(" ", strip=True) if name in _HEADING_TAGS else node.get_text("\n", strip=True)
        if not text:
            continue
        seen.add(id(node))
        if name in _HEADING_TAGS:
            blocks.append(f"{'#' * _HEADING_TAGS[name]} {text}")
        else:
            blocks.append(text)

    if not blocks:
        fallback = root.get_text("\n", strip=True)
        return fallback or ""
    return "\n\n".join(blocks)
