# Requires: offline. Rebuilds license-matrix.json from the authored table below.
"""Build the Common Pile component evidence matrix (authored audit judgments).

The classification table is the audit's judgment, written here once; the
document URLs and SHA-256 values come from ``hf-cards.tsv`` and
``github-files.tsv`` (documentation fetched 2026-10-02, never corpus payload).
Labels are evidence classes, not legal advice and not an operator decision.

    <python> docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/build_matrix.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPOSITORY = "common-pile/comma_v0.1_training_dataset"
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
RETRIEVED = "2026-10-02"

#: Sources read only through a summarizing fetch or a search-result summary
#: (the site refused direct fetches); weaker than the byte-hashed documents.
SUMMARIES = {
    "paper": ("https://arxiv.org/abs/2506.05209v1", "webfetch summary of abs + html v1"),
    "pdr-reuse": (
        "https://publicdomainreview.org/reusing-material/",
        "web search summary (homepage fetched; no license text on it)",
    ),
    "opl": (
        "https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
        "web search summary (direct fetch HTTP 403)",
    ),
    "foodista-terms": (
        "https://creativecommons.org/2009/01/23/foodista-cc-powered-cooking-encyclopedia/",
        "web search summary incl. en.wikipedia.org/wiki/Foodista.com (foodista.com HTTP 429)",
    ),
    "loc-rights": (
        "https://www.loc.gov/collections/selected-digitized-books/about-this-collection/rights-and-access/",
        "web search summary (direct fetch HTTP 403)",
    ),
    "xlm-cert": (
        "repo:recipes/mixtures/mix01_views.yaml",
        "XLM registry note: 9 real rows (news, libretexts, public_domain_review)",
    ),
}

# component: (content_fit, license_class, confidence, evidence ids, basis, caveats)
TABLE: dict[str, tuple[str, str, str, list[str], str, str]] = {
    "arxiv_abstracts": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_OPEN_LICENSE",
        "HIGH",
        ["hf:arxiv_abstracts", "comma-card"],
        "arXiv metadata distributed CC0",
        "research-paper genre already covered by FinePDFs/Essential science",
    ),
    "arxiv_papers": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:arxiv_papers", "comma-card"],
        "author-selected CC BY/BY-SA/CC0 at upload",
        "research-paper duplication; LaTeXML+Trafilatura conversion",
    ),
    "biodiversity_heritage_library": (
        "EXCLUDE_CONTENT_FIT",
        "MIXED_BUT_FILTERED_TO_OPEN",
        "MEDIUM",
        [
            "hf:biodiversity_heritage_library",
            "gh:sources/bhl/README.md",
            "gh:sources/bhl/license_whitelist.json",
        ],
        "BHL rights-statement whitelist (public domain / no known copyright restrictions)",
        "page-level OCR documents, taxonomic and non-English text; 'no known restrictions' is "
        "weaker than PD",
    ),
    "caselaw_access_project": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:caselaw_access_project"],
        "US court opinions, public-domain documents only",
        "government legal bulk; outside the prose intent",
    ),
    "cccc": (
        "EXCLUDE_CONTENT_FIT",
        "EXCLUDE_PROVENANCE",
        "LOW",
        ["hf:cccc", "hf:cccc_filtered", "comma-card"],
        "per-domain CC markers; top-1000 domains manually verified",
        "card: upstream dataset 'updated to remove instances of incorrect licensing'; the Comma "
        "training copy predates that and cannot be filtered (text-only rows); web genre duplicates "
        "Essential-Web/Nemotron/UltraX",
    ),
    "data_provenance_initiative": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:data_provenance_initiative"],
        "dataset-level license audits by the Data Provenance Initiative",
        "supervised task data (benchmark-like); not prose",
    ),
    "doab": (
        "PARTIAL_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        [
            "hf:doab",
            "hf:doab_filtered",
            "gh:sources/doab/README.md",
            "gh:sources/doab/cc_filter.py",
            "gh:filtering/mixer_configs/doab.json",
        ],
        "DOAB metadata CC BY/BY-SA + license statement required in front/back matter",
        "scholarly monographs overlap the FinePDFs PDF genre; Marker PDF-to-text; keyword-based "
        "license check; third-party figures/quotes inside CC books",
    ),
    "foodista": (
        "NEEDS_OPERATOR_REVIEW",
        "NEEDS_OPERATOR_REVIEW",
        "LOW",
        ["hf:foodista", "gh:sources/food/README.md", "foodista-terms"],
        "site-wide CC BY declaration",
        "database partly built by an automated web crawl and user submissions; user comments "
        "appended; "
        "site terms not fetched directly",
    ),
    "github_archive": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:github_archive"],
        "issue/PR text inherits Blue-Oak-approved repository licenses",
        "code-adjacent technical threads",
    ),
    "library_of_congress": (
        "NEEDS_OPERATOR_REVIEW",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:library_of_congress", "gh:sources/loc_books/README.md", "loc-rights"],
        "LoC 'Selected Digitized Books' collection designated public domain (US)",
        "OCR text; per-item rights advisories (e.g. no renewal found) are not carried in rows; "
        "card's "
        "'intended CC-BY-4.0' statement is a dataset-level claim",
    ),
    "libretexts": (
        "FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "HIGH",
        [
            "hf:libretexts",
            "hf:libretexts_filtered",
            "gh:sources/libretexts/to_dolma.py",
            "gh:sources/libretexts/README.md",
            "gh:filtering/mixer_configs/libretexts.json",
            "xlm-cert",
        ],
        "per-section page license tag must be ccby/ccbysa/gnufdl/publicdomain, else skipped",
        "LibreTexts pages are on the open web (C05 dedup vs Essential-Web/UltraX/FinePDFs); "
        "filtered "
        "card says 3.6 GB but Comma tokens (0.093B) and 122 MB compressed imply ~0.4 GB",
    ),
    "news": (
        "FIT",
        "MIXED_BUT_FILTERED_TO_OPEN",
        "MEDIUM",
        [
            "hf:news",
            "gh:sources/news/README.md",
            "gh:sources/news/build_index.py",
            "gh:filtering/mixer_configs/news.json",
            "xlm-cert",
        ],
        "site-level CC BY/BY-SA per Open Newswire; per-document license field assigned by site",
        "syndicated third-party articles possible; README site list differs from card (Liberty TV, "
        "Caravanserai); includes state-affiliated outlets",
    ),
    "oercommons": (
        "FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "HIGH",
        [
            "hf:oercommons",
            "gh:sources/oercommons/README.md",
            "gh:sources/oercommons/collect_search_results.py",
            "gh:sources/oercommons/to_dolma.py",
            "gh:filtering/mixer_configs/oercommons.json",
        ],
        "per-resource license mapped strictly to PD/CC BY/CC BY-SA",
        "worksheets/problem sets: benchmark-overlap risk for C05; tiny (0.012B tokens)",
    ),
    "peS2o": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:peS2o", "comma-card"],
        "openly licensed S2ORC papers",
        "research-paper duplication",
    ),
    "pre_1929_books": (
        "NEEDS_OPERATOR_REVIEW",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:pre_1929_books"],
        "US publication before 1929 per HathiTrust bibliographic records",
        "Internet Archive OCR text; US-only public-domain basis; catalog-derived publication data",
    ),
    "pressbooks": (
        "FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "HIGH",
        [
            "hf:pressbooks",
            "gh:sources/pressbooks/README.md",
            "gh:sources/pressbooks/process_search_results.py",
            "gh:sources/pressbooks/to_dolma.py",
            "gh:filtering/mixer_configs/pressbooks.json",
        ],
        "per-book catalog license mapped strictly to CC BY/CC BY-SA/CC0/PD",
        "book-level license; 'except where otherwise noted' third-party content possible; web "
        "overlap",
    ),
    "project_gutenberg": (
        "FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "HIGH",
        [
            "hf:project_gutenberg",
            "gh:sources/gutenberg/README.md",
            "gh:sources/gutenberg/possible-rights.py",
            "gh:sources/gutenberg/build-index.py",
            "gh:filtering/mixer_configs/project_gutenberg.json",
        ],
        "PG dcterms:rights 'Public domain in the USA.' plus PG19 special cases",
        "US-only public-domain basis; two items PG marks copyrighted were added by age; archaic "
        "language",
    ),
    "public_domain_review": (
        "FIT",
        "VERIFIED_OPEN_LICENSE",
        "HIGH",
        [
            "hf:public_domain_review",
            "gh:sources/public_domain_review/README.md",
            "gh:sources/public_domain_review/scrape.py",
            "pdr-reuse",
            "xlm-cert",
        ],
        "PDR essays: unquoted text CC BY-SA 4.0",
        "quoted passages are outside PDR's license (typically public-domain sources); 0.0017B "
        "tokens",
    ),
    "pubmed": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:pubmed"],
        "journal-designated CC BY/BY-SA/CC0",
        "research-paper duplication",
    ),
    "python_enhancement_proposals": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "HIGH",
        ["hf:python_enhancement_proposals"],
        "PEPs in the public domain; 5 OPL PEPs omitted",
        "technical/code-adjacent",
    ),
    "regulations": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:regulations"],
        "US federal agency documents",
        "government legal bulk",
    ),
    "stackexchange": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "HIGH",
        ["hf:stackexchange"],
        "CC BY-SA per post",
        "Stack Exchange technical QA",
    ),
    "stackv2_edu": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:stackv2_edu_filtered", "hf:stackv2"],
        "Stack v2 repository license detection, Blue Oak list",
        "code",
    ),
    "stackv2_html": (
        "EXCLUDE_CONTENT_FIT",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MEDIUM",
        ["hf:stackv2"],
        "Stack v2 repository license detection, Blue Oak list",
        "code/markup; no dedicated card read",
    ),
    "ubuntu_irc": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:ubuntu_irc"],
        "Ubuntu IRC logs released into the public domain (card)",
        "technical support chat; Ubuntu's own statement not fetched",
    ),
    "uk_hansard": (
        "NEEDS_OPERATOR_REVIEW",
        "NEEDS_OPERATOR_REVIEW",
        "MEDIUM",
        ["hf:uk_hansard", "opl"],
        "Open Parliament Licence v3.0 (UK Parliament)",
        "card also includes Scottish Parliament, Senedd (English and Welsh), NI Assembly and "
        "London "
        "Mayor's Questions, which are not UK Parliament information; OPL exempts personal data and "
        "third-party rights; attribution statement required",
    ),
    "usgpo": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:usgpo"],
        "US federal government works via GovInfo",
        "government bulk",
    ),
    "uspto": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_PUBLIC_DOMAIN",
        "MEDIUM",
        ["hf:uspto"],
        "US patents as government works",
        "patent bulk; card metadata names CC BY, inconsistent with the PD basis",
    ),
    "wikimedia": (
        "EXCLUDE_CONTENT_FIT",
        "VERIFIED_OPEN_LICENSE",
        "HIGH",
        ["hf:wikimedia"],
        "Wikimedia wikis CC BY-SA",
        "Wikipedia duplication of FineWiki/Wiki-Rewrite; text-only rows cannot isolate "
        "Wikibooks/Wikivoyage",
    ),
    "wikiteam": (
        "EXCLUDE_CONTENT_FIT",
        "MIXED_BUT_FILTERED_TO_OPEN",
        "LOW",
        ["hf:wikiteam"],
        "Internet Archive item metadata CC BY/BY-SA/PD",
        "card acknowledges license laundering (lyrics/transcript wikis removed heuristically)",
    ),
    "youtube": (
        "PARTIAL_FIT",
        "NEEDS_OPERATOR_REVIEW",
        "MEDIUM",
        ["hf:youtube", "gh-yt:README.md", "gh:filtering/mixer_configs/youtube.json", "paper"],
        "uploader CC BY on ~2,000 manually curated original-content channels",
        "project states CC laundering on YouTube is rampant; curated channel list not published; "
        "Whisper ASR text (machine transcription errors)",
    ),
}


def _rows(name: str) -> list[list[str]]:
    with (HERE / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))[1:]


def main() -> int:
    sources: dict[str, dict[str, Any]] = {}
    for repo, sha, size, digest in _rows("hf-cards.tsv"):
        if sha == "NOT_FOUND":
            continue
        name = repo.split("/", 1)[1]
        key = "comma-card" if name == "comma_v0.1_training_dataset" else f"hf:{name}"
        sources[key] = {
            "url": f"https://huggingface.co/datasets/{repo}/blob/{sha}/README.md",
            "sha256": digest,
            "bytes": int(size),
            "method": "direct bytes",
            "retrieved": RETRIEVED,
        }
    for at, path, size, digest in _rows("github-files.tsv"):
        repo, commit = at.split("@", 1)
        key = ("gh-yt:" if repo == "nkandpa2/youtube-commons" else "gh:") + path
        sources[key] = {
            "url": f"https://github.com/{repo}/blob/{commit}/{path}",
            "sha256": digest,
            "bytes": int(size),
            "method": "direct bytes",
            "retrieved": RETRIEVED,
        }
    for key, (url, method) in SUMMARIES.items():
        sources[key] = {"url": url, "method": method, "retrieved": RETRIEVED}
    components: dict[str, Any] = {}
    for name, (fit, license_class, confidence, refs, basis, caveats) in sorted(TABLE.items()):
        missing = [r for r in refs if r not in sources]
        if missing:
            raise SystemExit(f"{name}: unknown evidence {missing}")
        components[name] = {
            "content_fit": fit,
            "license_class": license_class,
            "provenance_confidence": confidence,
            "evidence": refs,
            "license_basis": basis,
            "caveats": caveats,
        }
    used = sorted({r for c in components.values() for r in c["evidence"]})
    matrix: dict[str, Any] = {
        "kind": "mix01_component_evidence_matrix",
        "version": 1,
        "repository": REPOSITORY,
        "revision": REVISION,
        "audit": "docs/implementation/reports/COMMON-PILE-PROSE-ALLOWLIST-AUDIT.md",
        "legal_advice": False,
        "row_schema_note": (
            "Pinned rows are exactly {'text': str}: no per-document license, id or metadata "
            "survives in this repository, so every decision is per top-level component."
        ),
        "sources": {k: sources[k] for k in sorted(sources) if k in used},
        "components": components,
    }
    out = HERE / "license-matrix.json"
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    print(f"{len(components)} components, {len(matrix['sources'])} cited sources -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
