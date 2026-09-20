"""Auditable quality reporting and sanitized paired preview rendering.

Adheres to C03 and Amendment 7.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.pii import redact_sensitive_text
from xlm.data.cleaning.pipeline import PipelineExecutionSummary

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def sanitize_inert_text(text: str, max_chars: int = 400) -> str:
    """Sanitize and format text for inert preview display across Markdown and HTML.

    Adheres strictly to Amendment 7:
    - Redacts sensitive credentials, tokens, and PII.
    - Strips ANSI control sequences.
    - Escapes Markdown backticks.
    - Truncates to bounded character length.
    """
    if not text:
        return ""
    # 1. Redact credentials and PII
    redacted = redact_sensitive_text(text[:max_chars])
    # 2. Strip ANSI escape sequences
    clean_ansi = ANSI_ESCAPE_RE.sub("", redacted)
    # 3. Escape backticks so markdown code blocks are not broken
    clean = clean_ansi.replace("```", "\\`\\`\\`")
    return clean


@dataclass
class PairedSampleReview:
    """Sanitized before/after text pairing for auditable quality review."""

    doc_id: str
    source_id: str
    stage: str
    action: str
    before_text_preview: str
    after_text_preview: str
    reasons: list[str]


class QualityReporter:
    """Generates auditable quality reports and metrics summaries.

    Adheres strictly to Amendments 6 and 7:
    - Honest statistics: separate document and byte yield, safe zero handling without NaN/Inf.
    - Complete escaping: all text samples escaped (HTML/Markdown/Terminal) and redacted.
    - Paired review displays for audited samples.
    """

    def __init__(self, summary: PipelineExecutionSummary) -> None:
        self.summary = summary
        self.review_samples: list[PairedSampleReview] = []

    def add_review_sample(
        self,
        doc_before: CanonicalDocument,
        doc_after: CanonicalDocument | None,
        action: str,
        stage: str,
        reasons: list[str],
    ) -> None:
        """Add a sanitized before/after review sample."""
        before_preview = sanitize_inert_text(doc_before.text)
        after_preview = (
            sanitize_inert_text(doc_after.text)
            if doc_after and doc_after.text
            else "[DOCUMENT_DROPPED_OR_EMPTY]"
        )

        sample = PairedSampleReview(
            doc_id=doc_before.doc_id,
            source_id=doc_before.source_id,
            stage=stage,
            action=action,
            before_text_preview=before_preview,
            after_text_preview=after_preview,
            reasons=reasons,
        )
        self.review_samples.append(sample)

    def generate_markdown(self) -> str:
        """Generate a complete, sanitized Markdown quality report."""
        s = self.summary
        doc_pct = s.document_yield_ratio * 100.0
        byte_pct = s.byte_yield_ratio * 100.0

        md: list[str] = [
            f"# Data Cleaning & Quality Report: {s.domain_preset}",
            "",
            f"**Pipeline Hash:** `{s.pipeline_hash}`  ",
            f"**Domain Preset:** `{s.domain_preset}`  ",
            f"**Execution Duration:** `{s.elapsed_seconds}s`  ",
            f"**Partial Sample:** `{s.is_partial_sample}` "
            f"(Declared max: `{s.declared_max_docs}`)  ",
            "",
            "## 1. Overall Yield Statistics",
            "",
            "| Metric | Input | Retained | Yield (%) |",
            "|---|---|---|---|",
            f"| **Documents** | {s.total_input_docs:,} "
            f"| {s.total_output_docs:,} | {doc_pct:.2f}% |",
            f"| **UTF-8 Bytes** | {s.total_input_bytes:,} "
            f"| {s.total_output_bytes:,} | {byte_pct:.2f}% |",
            f"| **Rejected Docs** | - | {s.total_rejected_docs:,} | - |",
            "",
            "## 2. Stage-by-Stage Attrition",
            "",
            "| Stage | Transform | In Docs | Out Docs | Rejections | In Bytes | Out Bytes | "
            "Duration (ms) |",
            "|---|---|---|---|---|---|---|---|",
        ]

        for st in s.stage_metrics:
            md.append(
                f"| `{st.stage_name}` | `{st.transform_id}:v{st.transform_version}` | "
                f"{st.input_docs:,} | {st.output_docs:,} | {st.rejected_docs:,} | "
                f"{st.input_bytes:,} | {st.output_bytes:,} | {st.duration_ms:.1f} |"
            )

        md.extend(
            [
                "",
                "## 3. Rejection Reasons Breakdown",
                "",
                "| Reason | Occurrence Count |",
                "|---|---|",
            ]
        )

        if not s.reason_counts:
            md.append("| *(None - 100% accepted)* | 0 |")
        else:
            for r, c in sorted(s.reason_counts.items(), key=lambda x: -x[1]):
                md.append(f"| `{r}` | {c:,} |")

        if self.review_samples:
            md.extend(
                [
                    "",
                    "## 4. Paired Before / After Audit Samples",
                    "",
                ]
            )
            for i, samp in enumerate(self.review_samples, start=1):
                md.extend(
                    [
                        f"### Sample {i}: `{samp.doc_id}` ({samp.action} at `{samp.stage}`)",
                        f"- **Reasons:** `{samp.reasons}`",
                        "- **Before:**",
                        "```text",
                        samp.before_text_preview,
                        "```",
                        "- **After:**",
                        "```text",
                        samp.after_text_preview,
                        "```",
                        "",
                    ]
                )

        return "\n".join(md)

    def generate_html(self) -> str:
        """Generate a complete, escaped, standalone HTML quality report."""
        md_text = self.generate_markdown()
        escaped_body = html.escape(md_text)

        html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>XLM Quality Report - {self.summary.domain_preset}</title>
<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
  margin: 2rem; line-height: 1.5; color: #24292e; background: #fff;
}}
pre {{
  background: #f6f8fa; padding: 1rem; border-radius: 6px;
  overflow-x: auto; white-space: pre-wrap; word-wrap: break-word;
}}
</style>
</head>
<body>
<pre>{escaped_body}</pre>
</body>
</html>
"""
        return html_out

    def save_reports(self, output_dir: Path) -> tuple[Path, Path]:
        """Save Markdown and HTML reports to output directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / "quality_report.md"
        html_path = output_dir / "quality_report.html"

        md_path.write_text(self.generate_markdown(), encoding="utf-8")
        html_path.write_text(self.generate_html(), encoding="utf-8")
        return md_path, html_path
