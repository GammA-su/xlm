"""Authored adversarial fixtures for the Phase-A quality audit (no real corpus text)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical

PROSE = (
    "Rivers shape the valleys they cross. Over long periods, the water carries "
    "sediment downstream, cutting channels and building plains where the current "
    "slows. Engineers study these processes to plan bridges and flood defences."
)

TEXTS: dict[str, str] = {
    # HTML / markup
    "html_page": (
        "<!DOCTYPE html>\n<html><head><title>Shop</title><script>var x = 1;</script>"
        "<style>p{color:red}</style></head>\n<body><nav>Home | About</nav>\n"
        "<div class='c'><p>Buy now and save.</p><a href='/x'>More</a></div>\n"
        "<footer>&copy; 2023 Shop. All rights reserved.</footer></body></html>"
    ),
    "light_markup": (
        "The <b>bold</b> claim and an &amp; entity appear in otherwise ordinary prose "
        "about rivers and the valleys that they carve."
    ),
    "cpp_templates": (
        "template <typename T>\nstd::vector<std::string> names;\n"
        "std::map<int, std::vector<T>> index;\nif (a < b && c > d) { return; }\n"
        "auto p = std::make_unique<Node>(value);\n"
    ),
    "xml_code": (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <entry key="alpha">1</entry>\n'
        '  <entry key="beta">2</entry>\n</config>\n'
    ),
    "math_angles": (
        "For every x with 0 < x < 1 we have x^2 < x, and if a<b then a+c<b+c. "
        "Similarly n > 3 > 2 holds, and the bound |f(x)| <= M follows."
    ),
    # Repetition
    "char_spam": "Wow" + "!" * 300 + " what a deal" + "?" * 40,
    "markdown_separator": "# Results\n\n"
    + PROSE
    + "\n\n"
    + "-" * 40
    + "\n\n"
    + PROSE.replace("Rivers", "Streams"),
    "ascii_art": "+---+---+\n| a | b |\n+---+---+\n| c | d |\n+---+---+\n",
    "repeated_paragraphs": "\n\n".join([PROSE] * 5),
    "code_braces": (
        "int main() {\n    for (int i = 0; i < 3; i++) {\n        if (i) {\n"
        "            work(i);\n        }\n    }\n    return 0;\n}\n"
        "void helper() {\n    if (ready) {\n        run();\n    }\n}\n"
    ),
    "generated_loop": "the quick brown fox jumps over the lazy dog " * 120,
    # Unicode
    "accented_latin": (
        "Café crème brûlée à la française: une façade naïve, un rôle clé, "
        "Ærøskøbing og São Paulo, Ñandú y pingüino."
    ),
    "japanese": "日本語のテキストは正常です。東京は日本の首都であり、多くの人が住んでいます。",
    "emoji": "Family 👨\u200d👩\u200d👧 trip with flags 🇫🇷 and 🇯🇵, thumbs 👍🏽 up!",
    "combining_marks": "Cafe\u0301 and nai\u0308ve spelled with combining accents.",
    "control_junk": "abc\x00def\x07ghi\x1b[0m broken \ufffd text \u0085 end",
    "mojibake": "Itâ€™s a cafÃ© with naÃ¯ve Ã©clairs â€“ really.",
    "format_junk": "zero\u200bwidth, bom\ufeffinside, bidi\u202eevil\u202c, pua\ue000 and \ufdd0.",
    # OCR / PDF
    "page_headers": "\n".join(
        part
        for n in range(5)
        for part in ("Journal of Applied Things, Vol. 3", PROSE, str(n + 1), "")
    ),
    "hyphenation": (
        "The experi-\nment was success-\nful, and the measure-\nments agreed with "
        "the predic-\ntion of the model."
    ),
    "page_numbers": "Introduction\n12\n" + PROSE + "\n- 13 -\n" + PROSE + "\nPage 14 of 20\n",
    "clean_scientific": (
        "We measure the energy E = mc^2 of each particle. The resulting distribution "
        "agrees with the model within two standard deviations, as shown in Table 2."
    ),
    "single_char_lines": "\n".join("abcdefghijklmnopqrstuvwxyz"),
    # Composition
    "python_code": (
        "import os\n\n\ndef read(path):\n    with open(path) as handle:\n"
        "        return handle.read()\n\n\nclass Loader:\n    pass\n"
    ),
    "latex_equation": r"\frac{a}{b} + \sum_{i=1}^{n} x_i = \int_0^1 f(x)\,dx \leq \alpha",
    "markdown_table": "| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |\n",
    "url_list": "https://example.org/a\nhttps://example.org/b\nwww.example.com/c\n",
    "ordinary_prose": PROSE,
    "garbage_symbols": "#$%^&*()_+{}|:<>?~` " * 12,
    "boilerplate": (
        "Accept all cookies\nThis website uses cookies to improve your experience.\n"
        "Privacy Policy\nTerms of Use\nSubscribe to our newsletter\nLogin\nHome\n"
        "Contact us\n© 2024 Example Corp. All rights reserved.\nFollow us on Twitter\n"
    ),
    # Size
    "empty": "",
    "whitespace_only": " \n\t \n",
    "tiny": "ok",
}
# Distinctive substrings that must never appear in a content-free artifact.
CANARIES = (
    "Buy now",
    "quick brown fox",
    "日本語",
    "Journal of Applied",
    "naÃ¯ve",
    "Ærøskøbing",
    "Subscribe to our newsletter",
    "engineers study",
    "Engineers study",
)


def document(
    doc_id: str, text: str, row: int, *, metadata: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="authored",
        source_revision="authored-revision",
        source_file="authored",
        source_row=row,
        raw_hash=hashlib.sha256(text.encode()).hexdigest(),
        clean_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        utf8_byte_count=len(text.encode("utf-8")),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata=dict(metadata or {}),
        parent_ids=[],
        license_reference="authored",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    ).to_dict()


def write_file(path: Path, rows: Sequence[Mapping[str, Any]]) -> bytes:
    raw = b"".join(canonical.canonical_bytes(dict(r)) + b"\n" for r in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def build_corpus(root: Path, layout: Mapping[str, Sequence[Mapping[str, Any]]]) -> Path:
    """``layout`` maps ``component/view/name`` -> rows; returns the manifest path."""
    data = root / "data"
    files = []
    for key in sorted(layout):
        component, view, name = key.split("/")
        rows = layout[key]
        path = data / "canonical" / component / view / name / "documents.jsonl"
        raw = write_file(path, rows)
        files.append(
            {
                "path": path.relative_to(data).as_posix(),
                "source_key": component.split("_")[0],
                "source_id": "authored",
                "source_revision": "authored-revision",
                "component": component,
                "view": view,
                "upstream_component": None,
                "source_file": "authored",
                "documents_sha256": hashlib.sha256(raw).hexdigest(),
                "file_bytes": len(raw),
                "canonical_bytes": sum(int(r["utf8_byte_count"]) for r in rows),
                "documents": len(rows),
            }
        )
    manifest: dict[str, Any] = {
        "kind": "authored_c05_input",
        "data_root": str(data.resolve()),
        "files": files,
        "sources": [],
    }
    manifest["digest"] = canonical.self_digest(manifest)
    target = root / "manifest.json"
    canonical.write_canonical_json(target, manifest)
    return target


def standard_layout() -> dict[str, list[dict[str, Any]]]:
    """Four files over three components with every adversarial text and filler prose."""
    names = sorted(TEXTS)
    web = [document(f"web-{n:03d}", TEXTS[k], n + 1) for n, k in enumerate(names)]
    filler = [
        document(
            f"pdf-{n:03d}",
            PROSE.replace("Rivers", f"Rivers {n}") + f"\n{n + 1}\n",
            n + 1,
            metadata={
                "upstream_full_doc_lid": "eng_Latn",
                "upstream_full_doc_lid_score": 0.5 + n / 100,
                "language_provenance": "authored routing label",
            },
        )
        for n in range(40)
    ]
    stories = [
        document(f"story-{n:03d}", f"Once upon a time {n} a fox met a crow.", n + 1)
        for n in range(30)
    ]
    scored = [
        document(
            f"ew-{n:03d}",
            TEXTS[names[n % len(names)]] + f" {n}",
            n + 1,
            metadata={"fasttext_english": round(0.3 + n / 100, 4)},
        )
        for n in range(40)
    ]
    return {
        "essential_prose/essential_prose/a": web,
        "essential_prose/essential_prose/b": scored,
        "finepdfs_en/eng_Latn/a": filler,
        "simple_stories/default/a": stories,
    }
