"""Release auditing and final-data absence verification (P21, A37).

A release directory is checked item by item: export integrity, code/lock/config
hashes, data-use and admission records, lineage, rights/provenance (unknown
stays BLOCKED, never silently certified), reproducibility instructions,
benchmark exposure labels, parameter/compute disclosures, attribution files,
and absence of secrets or final labels. Separately,
:func:`verify_no_final_examples` scans Hugging Face caches for materialized
official final-split artifacts and fails loudly when any exist.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RELEASE_AUDIT_VERSION = "1"

# Official repos whose FINAL splits must never appear in development caches.
# Final splits per the evaluation policy: ARC test, HellaSwag/PIQA validation.
FINAL_SPLIT_MARKERS: dict[str, dict[str, str]] = {
    "allenai/ai2_arc": {"config": "ARC-Easy", "split": "test"},
    "Rowan/hellaswag": {"config": "default", "split": "validation"},
    "baber/piqa": {"config": "plain_text", "split": "validation"},
}

SECRET_BASENAMES = (
    "*.pem",
    "*.key",
    "*api*token*",
    "*auth*token*",
    "*secret*",
    "*credential*",
    "*password*",
    ".env",
)

FINAL_LABEL_KEYS = frozenset(
    {
        "final_predictions",
        "final_labels",
        "sealed_predictions",
        "sealed_labels",
        "test_labels",
        "per_item_final",
    }
)


class ReleaseAuditError(RuntimeError):
    """Raised when a release audit cannot be performed (not when it fails items)."""


@dataclass
class AuditFinding:
    """One checklist verdict."""

    check: str
    status: str  # 'pass' | 'fail' | 'blocked'
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReleaseAudit:
    """Complete release audit with an overall verdict."""

    audit_version: str
    release_dir: str
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        statuses = {f.status for f in self.findings}
        if "fail" in statuses:
            return "HOLD"
        if "blocked" in statuses:
            return "BLOCKED"
        return "RELEASE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_version": self.audit_version,
            "release_dir": self.release_dir,
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "audited_at": datetime.now(UTC).isoformat(),
        }


def _finding(findings: list[AuditFinding], check: str, status: str, detail: str) -> None:
    findings.append(AuditFinding(check=check, status=status, detail=detail))


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def audit_release(release_dir: Path | str) -> ReleaseAudit:
    """Audit a release directory. Unknown rights stay BLOCKED, never certified."""
    root = Path(release_dir)
    findings: list[AuditFinding] = []
    if not root.is_dir():
        raise ReleaseAuditError(f"release directory not found: {root}")

    # 1. Export integrity (P20 bundles verify through their own manifests).
    export_manifest = _read_json(root / "export_manifest.json")
    if export_manifest is None:
        _finding(findings, "export_integrity", "fail", "export_manifest.json missing or unreadable")
    else:
        files = export_manifest.get("files", [])
        absent = [
            entry.get("path", "?")
            for entry in files
            if isinstance(entry, dict) and not (root / str(entry.get("path", ""))).is_file()
        ]
        if absent:
            _finding(findings, "export_integrity", "fail", f"bundled files missing: {absent[:5]}")
        else:
            _finding(
                findings,
                "export_integrity",
                "pass",
                f"{len(files)} bundled files present with recorded hashes",
            )

    # 2. Code / lockfile / config hashes.
    hashes = _read_json(root / "code_hashes.json")
    if hashes is None or not hashes.get("uv_lock_sha256"):
        _finding(
            findings,
            "code_lock_config_hashes",
            "fail",
            "code_hashes.json with uv_lock_sha256 required",
        )
    else:
        _finding(
            findings,
            "code_lock_config_hashes",
            "pass",
            f"lockfile pinned ({str(hashes['uv_lock_sha256'])[:12]})",
        )

    # 3. Data-use and admission records.
    admission = _read_json(root / "admission.json")
    if admission is None:
        _finding(findings, "data_use_admission", "fail", "admission.json missing")
    else:
        unreviewed = [
            str(s.get("source_id", "?"))
            for s in admission.get("sources", [])
            if isinstance(s, dict) and s.get("license_review") in (None, "pending", "unknown")
        ]
        if unreviewed:
            _finding(
                findings,
                "data_use_admission",
                "blocked",
                f"license review pending/unknown for: {unreviewed[:5]}",
            )
        else:
            _finding(
                findings,
                "data_use_admission",
                "pass",
                f"{len(admission.get('sources', []))} sources reviewed",
            )

    # 4. Source lineage.
    lineage = _read_json(root / "lineage.json")
    if lineage is None or not lineage.get("sources"):
        _finding(findings, "source_lineage", "fail", "lineage.json with sources required")
    else:
        _finding(findings, "source_lineage", "pass", f"{len(lineage['sources'])} lineage entries")

    # 5. Rights / provenance evidence.
    rights = _read_json(root / "rights.json")
    if rights is None:
        _finding(
            findings, "rights_provenance", "blocked", "rights.json missing: provenance unknown"
        )
    else:
        unknown = [
            str(e.get("source_id", "?"))
            for e in rights.get("entries", [])
            if isinstance(e, dict) and e.get("status") in (None, "unknown", "pending")
        ]
        if unknown:
            _finding(
                findings,
                "rights_provenance",
                "blocked",
                f"unknown rights/provenance for: {unknown[:5]}; never silently certified",
            )
        else:
            _finding(
                findings,
                "rights_provenance",
                "pass",
                f"{len(rights.get('entries', []))} rights entries resolved",
            )

    # 6. Reproducibility instructions.
    if (root / "REPRODUCE.md").is_file():
        _finding(findings, "reproducibility", "pass", "REPRODUCE.md present")
    else:
        _finding(findings, "reproducibility", "fail", "REPRODUCE.md missing")

    # 7. Benchmark exposure labels.
    exposure = _read_json(root / "exposure.json")
    if exposure is None or "tier" not in exposure:
        _finding(findings, "benchmark_exposure", "fail", "exposure.json with tier labels required")
    else:
        _finding(
            findings,
            "benchmark_exposure",
            "pass",
            f"tier={exposure.get('tier')}; final={exposure.get('final_scores', 'none')}",
        )

    # 8. Parameter and compute disclosures.
    disclosure = _read_json(root / "disclosure.json")
    if disclosure is None or disclosure.get("unique_parameters") is None:
        _finding(
            findings,
            "parameter_compute_disclosure",
            "fail",
            "disclosure.json with unique_parameters required",
        )
    else:
        _finding(
            findings,
            "parameter_compute_disclosure",
            "pass",
            f"{disclosure.get('unique_parameters')} params; "
            f"compute={disclosure.get('compute_seconds', 'unmeasured')}",
        )

    # 9. Attribution file.
    if (root / "ATTRIBUTION").is_file() or (root / "ATTRIBUTION.md").is_file():
        _finding(findings, "attribution", "pass", "attribution file present")
    else:
        _finding(findings, "attribution", "fail", "ATTRIBUTION file missing")

    # 10. Secrets scan over release files (text files only, bounded).
    secret_hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        name = path.name.lower()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_BASENAMES):
            secret_hits.append(path.relative_to(root).as_posix())
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "hf_" in text and len(text) < 200_000:
            import re as _re

            if _re.search(r"hf_[A-Za-z0-9]{20,}", text):
                secret_hits.append(f"{path.relative_to(root).as_posix()} (embedded token)")
    if secret_hits:
        _finding(findings, "secrets_absent", "fail", f"secret-like content: {secret_hits[:5]}")
    else:
        _finding(findings, "secrets_absent", "pass", "no secret patterns in release files")

    # 11. Final-label scan: label-bearing keys must not ship in a release.
    label_hits: list[str] = []
    for path in sorted(root.rglob("*.json")):
        if path.stat().st_size > 5 * 1024 * 1024:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        found = _find_keys(data, FINAL_LABEL_KEYS)
        if found:
            label_hits.append(f"{path.relative_to(root).as_posix()}: {sorted(found)[:3]}")
    if label_hits:
        _finding(findings, "final_labels_absent", "fail", f"label-bearing keys: {label_hits[:3]}")
    else:
        _finding(findings, "final_labels_absent", "pass", "no label-bearing keys in release JSON")

    return ReleaseAudit(
        audit_version=RELEASE_AUDIT_VERSION,
        release_dir=str(root),
        findings=findings,
    )


def _find_keys(obj: Any, keys: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in keys:
                found.add(str(key))
            found |= _find_keys(value, keys)
    elif isinstance(obj, list):
        for item in obj:
            found |= _find_keys(item, keys)
    return found


@dataclass
class FinalDataFinding:
    """One materialized official final-split artifact found in a cache."""

    repository: str
    config: str
    split: str
    path: str
    size_bytes: int
    modified_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sanitized_repo_dir(cache_dir: Path, repository: str) -> Path | None:
    candidates = [
        cache_dir / repository.replace("/", "___"),
        cache_dir / f"datasets--{repository.replace('/', '--')}",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def verify_no_final_examples(
    hf_caches: list[Path | str] | None = None,
    repos: Mapping[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Scan Hugging Face caches for materialized official final-split artifacts.

    Looks for the final splits named by the evaluation policy (ARC-Easy test,
    HellaSwag/PIQA validation) as arrow files or split parquet files under the
    official repositories' cache directories. Any hit fails the check with the
    file list; absence passes. This is a cache-content check, not a proof that
    bytes were never read -- that distinction is recorded, not hidden.
    """
    repos = repos if repos is not None else FINAL_SPLIT_MARKERS
    roots: list[Path] = []
    if hf_caches:
        roots.extend(Path(c) for c in hf_caches)
    else:
        home = Path.home()
        roots.append(home / ".cache" / "huggingface" / "datasets")
        roots.append(home / ".cache" / "huggingface" / "hub")

    findings: list[FinalDataFinding] = []
    scanned = 0
    for repository, marker in repos.items():
        split = marker["split"]
        for root in roots:
            base = _sanitized_repo_dir(root, repository)
            if base is None:
                continue
            scanned += 1
            slug = repository.split("/")[-1].replace("-", "_").lower()
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                name = path.name.lower()
                hit = name in (
                    f"{split}.arrow",
                    f"{split}.parquet",
                    f"{slug}-{split}.arrow",
                    f"{slug}-{split}.parquet",
                ) or (name.startswith(f"{split}-") and name.endswith(".parquet"))
                if not hit:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                findings.append(
                    FinalDataFinding(
                        repository=repository,
                        config=marker["config"],
                        split=split,
                        path=str(path),
                        size_bytes=stat.st_size,
                        modified_at=str(stat.st_mtime),
                    )
                )
    return {
        "repos_checked": sorted(repos),
        "cache_roots_scanned": scanned,
        "findings": [f.to_dict() for f in findings],
        "clean": not findings,
        "note": "cache-content check only; absence here does not prove bytes were "
        "never read elsewhere, and presence proves fetch, not exposure",
    }


def purge_final_split_artifacts(
    findings: list[Mapping[str, Any]],
    require_confirm: bool = True,
) -> dict[str, Any]:
    """Delete materialized final-split cache files. Requires explicit confirmation.

    Never called implicitly by verification: purging another tool's cache is a
    deliberate operator action with a record, not a side effect.
    """
    if require_confirm:
        raise ReleaseAuditError(
            "purging shared caches requires require_confirm=False passed explicitly by the operator"
        )
    removed: list[str] = []
    missing: list[str] = []
    for finding in findings:
        path = Path(str(finding.get("path", "")))
        if path.is_file():
            path.unlink()
            removed.append(str(path))
        else:
            missing.append(str(path))
    return {"removed": sorted(removed), "already_missing": sorted(missing)}
