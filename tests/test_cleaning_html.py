"""Unit tests for conservative HTML extraction and math anti-stripping.

Adheres to C03 and Amendment 3.
"""

from __future__ import annotations

from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.html import HtmlExtractionTransform
from xlm.data.normalization import compute_sha256


def create_sample_doc(
    doc_id: str,
    text: str,
    document_kind: str = "prose",
    source_metadata: dict[str, Any] | None = None,
) -> CanonicalDocument:
    b = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_html",
        source_revision="rev_1",
        source_file="test.html",
        source_row=1,
        raw_hash=compute_sha256(b),
        clean_hash=compute_sha256(b),
        text=text,
        utf8_byte_count=len(b),
        language="en",
        language_confidence=1.0,
        document_kind=document_kind,
        source_metadata=source_metadata or {},
        parent_ids=[],
        license_reference="mit",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_true_html_extraction() -> None:
    """Verify standard HTML markup is extracted, entities decoded, and tags stripped."""
    transform = HtmlExtractionTransform()
    html_text = (
        "<!DOCTYPE html><html><head><title>Test Title</title></head><body>"
        "<h1>Main Heading</h1>"
        "<p>This is the first paragraph with "
        "<a href='https://example.com'>a link</a> &amp; special "
        "characters.</p>"
        "<div><p>Second paragraph inside a div.</p></div>"
        "</body></html>"
    )
    doc = create_sample_doc("doc_html", html_text, source_metadata={"content_type": "html"})
    res = transform.apply(doc)

    assert res.document is not None
    extracted = res.document.text
    assert "<!DOCTYPE html>" not in extracted
    assert "<p>" not in extracted
    assert "href=" not in extracted
    assert "Main Heading" in extracted
    assert "first paragraph with a link & special characters." in extracted
    assert "Second paragraph inside a div." in extracted


def test_mathematical_inequalities_never_stripped() -> None:
    """Verify ordinary text with mathematical comparisons (<, >) is never treated as HTML."""
    transform = HtmlExtractionTransform()
    math_text = (
        "In real analysis, consider intervals where 0 < x < 1 and y > 2. "
        "Furthermore, for ordered fields: if a < b and b < c, then a < c. "
        "Also in algorithms: while i < n and j > 0: do_something()."
    )
    doc = create_sample_doc("doc_math", math_text, document_kind="math")
    res = transform.apply(doc)

    assert res.document is not None
    # Angle brackets must be completely intact!
    assert res.document.text == math_text
    assert "0 < x < 1" in res.document.text
    assert "a < b and b < c" in res.document.text
    assert "while i < n and j > 0:" in res.document.text


def test_code_document_bypasses_html_extraction() -> None:
    """Verify code documents with XML/HTML template tags are preserved verbatim."""
    transform = HtmlExtractionTransform()
    code_text = (
        "export function App() {\n"
        "    return (\n"
        "        <div>\n"
        "            <h1>Header</h1>\n"
        "            <CustomComponent prop={true} />\n"
        "        </div>\n"
        "    );\n"
        "}\n"
    )
    doc = create_sample_doc("doc_code_tags", code_text, document_kind="code")
    res = transform.apply(doc)

    assert res.document is not None
    # Verbatim JSX / template syntax preserved
    assert res.document.text == code_text


def test_script_and_style_dropping() -> None:
    """Verify script and style bodies are completely dropped without leaking code."""
    transform = HtmlExtractionTransform()
    html_with_scripts = (
        "<html><body>"
        "<p>Visible body text.</p>"
        "<script type='text/javascript'>const secret_var = 123; "
        "function track() { alert('leak'); }</script>"
        "<style>body { background: red; display: none; }</style>"
        "<p>Concluding paragraph.</p>"
        "</body></html>"
    )
    doc = create_sample_doc(
        "doc_scripts", html_with_scripts, source_metadata={"content_type": "html"}
    )
    res = transform.apply(doc)

    assert res.document is not None
    text = res.document.text
    assert "Visible body text." in text
    assert "Concluding paragraph." in text
    # Script and style contents must NOT appear anywhere
    assert "secret_var" not in text
    assert "alert('leak')" not in text
    assert "background: red" not in text
