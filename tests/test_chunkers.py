"""Tests for per-content-type chunkers and the chunker registry."""

from __future__ import annotations

from backend.chunkers import (
    chunk_markdown,
    chunk_pdf,
    chunk_plaintext,
    get_chunker,
    html_to_markdown_text,
    register_chunker,
)
from backend.chunkers.registry import _REGISTRY
from backend.documents.parsers import (
    _detect_column_split,
    _table_rows_to_markdown,
    parse_html,
    parse_pdf,
)


# ---------------------------------------------------------------- markdown

MD_DOC = """Intro paragraph before any heading. It has two sentences.

# Getting Started

Install the package. Run the setup script.

## Requirements

Python 3.11 or newer is required. A PostgreSQL database must be available.

# Reference

The reference section body.
"""


def test_markdown_chunks_start_with_heading_path() -> None:
    chunks = chunk_markdown(MD_DOC, chunk_size=700, overlap_sentences=1)
    by_path = {c.get("heading_path"): c for c in chunks}
    assert "Getting Started" in by_path
    assert "Getting Started > Requirements" in by_path
    assert "Reference" in by_path
    req = by_path["Getting Started > Requirements"]
    assert req["text"].startswith("Getting Started > Requirements\n\n")
    assert "Python 3.11" in req["text"]


def test_markdown_preamble_has_no_prefix() -> None:
    chunks = chunk_markdown(MD_DOC)
    preamble = chunks[0]
    assert "heading_path" not in preamble
    assert preamble["text"].startswith("Intro paragraph")


def test_markdown_offsets_point_to_body_span() -> None:
    chunks = chunk_markdown(MD_DOC)
    for c in chunks:
        body = MD_DOC[c["char_offset"] : c["char_end"]]
        # The chunk text is the (optionally prefixed) sentence-joined body.
        assert body.split()[0] in c["text"]
        assert body.split()[-1] in c["text"]


def test_markdown_large_section_recursively_split() -> None:
    body = " ".join(f"Sentence number {i} lives here." for i in range(60))
    doc = f"# Big Section\n\n{body}\n"
    chunks = chunk_markdown(doc, chunk_size=300, overlap_sentences=1)
    assert len(chunks) > 2
    for c in chunks:
        assert c["heading_path"] == "Big Section"
        assert c["text"].startswith("Big Section\n\n")


def test_markdown_heading_inside_code_fence_ignored() -> None:
    doc = (
        "# Real Heading\n\n"
        "Some text before code.\n\n"
        "```\n# not a heading\nprint('x')\n```\n\n"
        "Text after code.\n"
    )
    chunks = chunk_markdown(doc)
    assert all(c.get("heading_path") == "Real Heading" for c in chunks)
    joined = " ".join(c["text"] for c in chunks)
    assert "not a heading" in joined


def test_markdown_without_headings_falls_back_to_plaintext() -> None:
    text = "Just prose. " * 30
    md_chunks = chunk_markdown(text, chunk_size=200, overlap_sentences=1)
    pt_chunks = chunk_plaintext(text, chunk_size=200, overlap_sentences=1)
    assert [c["text"] for c in md_chunks] == [c["text"] for c in pt_chunks]


def test_markdown_headings_only_document_still_indexed() -> None:
    doc = "# One\n\n## Two\n\n## Three\n"
    chunks = chunk_markdown(doc)
    assert chunks
    assert "One" in chunks[0]["text"]


def test_markdown_table_becomes_standalone_chunk() -> None:
    doc = (
        "# Pricing\n\n"
        "Our plans are below.\n\n"
        "| Plan | Price |\n"
        "| --- | --- |\n"
        "| Free | $0 |\n"
        "| Pro | $49 |\n\n"
        "Contact sales for enterprise.\n"
    )
    chunks = chunk_markdown(doc)
    tables = [c for c in chunks if c.get("subtype") == "table"]
    assert len(tables) == 1
    assert "| Pro | $49 |" in tables[0]["text"]
    assert tables[0]["text"].startswith("Pricing\n\n")
    prose = [c for c in chunks if c.get("subtype") != "table"]
    assert all("| Pro |" not in c["text"] for c in prose)


# ---------------------------------------------------------------- pdf

def test_pdf_chunker_extracts_tables_as_chunks() -> None:
    text = (
        "First page prose. It talks about things.\n\n"
        "| Col A | Col B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
        "| 3 | 4 |\n\n"
        "Prose after the table."
    )
    chunks = chunk_pdf(text, chunk_size=1000, overlap_sentences=1)
    tables = [c for c in chunks if c.get("subtype") == "table"]
    assert len(tables) == 1
    assert "| 3 | 4 |" in tables[0]["text"]
    assert text[tables[0]["char_offset"] : tables[0]["char_end"]] == tables[0]["text"]


def test_pdf_oversized_table_split_repeats_header() -> None:
    header = "| Name | Value |\n| --- | --- |\n"
    rows = "\n".join(f"| row-{i:03d} | value-{i:03d} |" for i in range(60))
    chunks = chunk_pdf(header + rows, chunk_size=400, overlap_sentences=1)
    tables = [c for c in chunks if c.get("subtype") == "table"]
    assert len(tables) > 1
    for t in tables:
        assert t["text"].startswith("| Name | Value |")
    all_rows = "\n".join(t["text"] for t in tables)
    assert "row-000" in all_rows and "row-059" in all_rows


def test_pdf_single_pipe_line_is_not_a_table() -> None:
    text = "Some prose here. | just a pipe | in a sentence.\nMore prose follows here."
    chunks = chunk_pdf(text)
    assert all(c.get("subtype") != "table" for c in chunks)


# ---------------------------------------------------------------- html

HTML_DOC = """
<html><head><title>Fallback Title</title><script>var x = 1;</script></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<header class="site"><h1>Product Docs</h1></header>
<main>
  <h1>Product Docs</h1>
  <p>Welcome to the docs. Read them carefully.</p>
  <h2>Install</h2>
  <p>Run the installer. Restart the machine.</p>
  <table>
    <tr><th>OS</th><th>Supported</th></tr>
    <tr><td>Linux</td><td>yes</td></tr>
  </table>
</main>
<aside>Related links</aside>
<footer>Copyright 2026. All rights reserved.</footer>
<button>Sign up</button>
</body></html>
"""


def test_html_to_markdown_strips_boilerplate() -> None:
    text = html_to_markdown_text(HTML_DOC)
    assert "Home" not in text  # nav
    assert "Copyright" not in text  # footer
    assert "Related links" not in text  # aside
    assert "Sign up" not in text  # button
    assert "var x" not in text  # script


def test_html_to_markdown_preserves_heading_structure() -> None:
    text = html_to_markdown_text(HTML_DOC)
    assert "# Product Docs" in text
    assert "## Install" in text
    assert "| Linux | yes |" in text


def test_parse_html_then_markdown_chunker_yields_heading_paths() -> None:
    parsed = parse_html(HTML_DOC.encode("utf-8"))
    chunker = get_chunker("html")
    chunks = chunker(parsed)
    paths = {c.get("heading_path") for c in chunks}
    assert "Product Docs > Install" in paths


def test_parse_html_rejects_empty_content() -> None:
    try:
        parse_html(b"<html><body><nav>only nav</nav></body></html>")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty extractable content")


# ---------------------------------------------------------------- pdf parsing helpers

def test_table_rows_to_markdown() -> None:
    rendered = _table_rows_to_markdown([["Name", "Qty"], ["Apple", "3"], [None, "4"]])
    lines = rendered.splitlines()
    assert lines[0] == "| Name | Qty |"
    assert lines[1] == "| --- | --- |"
    assert "| Apple | 3 |" in lines
    assert "|  | 4 |" in lines


def _word(x0: float, x1: float) -> dict[str, float]:
    return {"x0": x0, "x1": x1}


def test_detect_column_split_two_columns() -> None:
    words = [_word(10, 90) for _ in range(30)] + [_word(110, 190) for _ in range(30)]
    split = _detect_column_split(words, 0, 200)
    assert split is not None
    left_edge, right_edge = split
    assert left_edge <= 100 <= right_edge


def test_detect_column_split_single_column() -> None:
    words = [_word(10, 190) for _ in range(40)]
    assert _detect_column_split(words, 0, 200) is None


def test_parse_pdf_falls_back_to_pypdf(monkeypatch) -> None:
    import backend.documents.parsers as parsers

    def boom(_content: bytes) -> str:
        raise RuntimeError("layout parser broken")

    monkeypatch.setattr(parsers, "_parse_pdf_layout", boom)
    from pypdf import PdfWriter
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    assert parsers.parse_pdf(buf.getvalue()) == ""


def test_parse_pdf_corrupted_raises_value_error() -> None:
    try:
        parse_pdf(b"not a pdf at all")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for corrupted PDF")


# ---------------------------------------------------------------- registry

def test_registry_maps_types() -> None:
    assert get_chunker("markdown").func is chunk_markdown  # type: ignore[attr-defined]
    assert get_chunker("html").func is chunk_markdown  # type: ignore[attr-defined]
    assert get_chunker("pdf").func is chunk_pdf  # type: ignore[attr-defined]
    assert get_chunker("plaintext").func is chunk_plaintext  # type: ignore[attr-defined]


def test_registry_unknown_type_falls_back_to_plaintext() -> None:
    chunker = get_chunker("unknown-type")
    chunks = chunker("One sentence. Another sentence.")
    assert chunks
    assert set(chunks[0].keys()) == {"text", "chunk_index", "char_offset", "char_end"}


def test_register_chunker_override_and_restore() -> None:
    original = _REGISTRY["plaintext"]
    try:
        register_chunker("plaintext", lambda text: [])
        assert get_chunker("plaintext")("anything") == []
    finally:
        register_chunker("plaintext", original)
