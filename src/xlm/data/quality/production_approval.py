"""The approved Phase-B dry run, re-verified before any cleaned byte is written.

Production cleaning trusts no number it is told: it binds to a COMPLETE dry-run
receipt and re-derives everything that receipt claims. Every check refuses on any
inconsistency:

- the receipt validates strictly (schema, self-digest, envelope) and equals the dry-run
  directory's binding; its policy status is ``POLICY_WITHIN_GUARDRAILS``; its result
  digest equals the operator-approved digest given on the command line;
- the dry run's cleaning policy is the frozen ``cleaning_policy_v2`` given now
  (version, digest, file SHA-256, Phase-A receipt, candidate file). Its detector
  semantics are the current ones. Its input manifest (digest, file SHA-256, kind, mode
  and totals), data root and every source identity equal the manifest given now;
- every dry-run artifact on disk has the SHA-256, size and record count of the receipt;
- every per-file dry-run unit loads against its manifest record and producer envelope,
  and the artifacts re-derived from those units are byte-identical to the published
  ones (same result digest and policy status).

What comes out is the dry run's exact per-file statistics. For each input file this
is the merged :class:`~xlm.data.quality.cleaning.CleanStats`: KEEP/DROP documents and
canonical bytes, rule marginals and exclusives, the severe-signal histogram, class and
OCR tables, exact rule combinations and strata. The global/component/rule accounting
of the verified published artifacts comes out too. Production compares its own
re-evaluation against both. The dry run's implementation identity is recorded, not
required to equal the current code: the cleaner is new code by construction, and
decision equality is proven per file instead.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import CleanStats
from xlm.data.quality.cleaning_policy import POLICY_V2, CompiledPolicy, PolicyError
from xlm.data.quality.cleaning_report import (
    ARTIFACTS,
    WITHIN_GUARDRAILS,
    build_artifacts,
    guardrails,
    impact,
    rule_impacts,
    summarize,
)
from xlm.data.quality.cleaning_runner import BINDING_FILE, load_receipt, load_unit
from xlm.data.quality.policy import CLASS_ORDER, POLICY_VERSION, policy_identity
from xlm.data.quality.scan import (
    MAX_MANIFEST_BYTES,
    InputManifest,
    QualityError,
    read_bounded,
)

MAX_DRY_ARTIFACT_BYTES = 256 * 1024**2
ACCOUNTING_SECTIONS = ("global", "components", "by_component", "rules", "guardrails")


class ApprovalError(QualityError):
    """Content-free refusal: the approved dry run does not authorize this cleaning."""


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise ApprovalError(f"approved dry run refused: {what}")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


@dataclass(frozen=True)
class ApprovedDryRun:
    path: Path
    receipt: Mapping[str, Any]
    binding: Mapping[str, Any]
    expected: Mapping[int, Mapping[str, Any]]  # ordinal -> merged CleanStats JSON
    accounting: Mapping[str, Any]  # from the verified published artifacts

    def identity(self) -> dict[str, Any]:
        return {
            "receipt_digest": self.receipt["digest"],
            "result_digest": self.receipt["result_digest"],
            "binding_digest": self.binding["digest"],
            "policy_status": self.receipt["policy_status"],
            "code_commit": self.receipt["implementation"]["code_commit"],
            "code_identity": self.receipt["implementation"]["code_identity"],
            "artifacts": {
                name: self.receipt["artifacts"][name]["sha256"]
                for name in sorted(self.receipt["artifacts"])
            },
        }

    @property
    def line_ceiling(self) -> int:
        return int(self.binding["line_ceiling"])


def check_policy_for_manifest(policy: CompiledPolicy, manifest: InputManifest) -> None:
    """The production policy: frozen v2, frozen from an audit of THIS manifest under the
    current detector semantics, with thresholds for every manifest component."""
    if policy.version != POLICY_V2:
        raise PolicyError(
            "cleaning policy refused: production cleaning requires cleaning_policy_v2"
        )
    if policy.params.ruleset.review_mask != 0:
        raise PolicyError("cleaning policy refused: production policy has a REVIEW outcome")
    phase_a = policy.provenance["phase_a"]
    if phase_a["input_manifest_digest"] != manifest.digest:
        raise PolicyError(
            "cleaning policy refused: thresholds were frozen from a Phase-A audit of a "
            "different input manifest"
        )
    if phase_a["detector_policy"] != {"version": POLICY_VERSION, "digest": policy_identity()}:
        raise PolicyError("cleaning policy refused: frozen under different detector semantics")
    for item in manifest.files:
        policy.rules_for(item.component)


def source_identities(manifest: InputManifest) -> list[dict[str, Any]]:
    return [
        {
            "path": f.path,
            "documents_sha256": f.documents_sha256,
            "file_bytes": f.file_bytes,
            "documents": f.documents,
        }
        for f in manifest.files
    ]


def _read_dry_binding(path: Path) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(
            read_bounded(path / BINDING_FILE, MAX_MANIFEST_BYTES, "dry-run binding")
        )
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise ApprovalError("approved dry run refused: binding is not strict JSON") from None
    _require(isinstance(body, dict), "binding schema")
    _require(body.get("digest") == canonical.self_digest(body), "binding self-digest")
    checked: dict[str, Any] = body
    return checked


def verify_approved_dry_run(
    path: Path,
    manifest: InputManifest,
    policy: CompiledPolicy,
    approved_result_digest: str,
    *,
    check: Callable[[], None] | None = None,
    progress: Callable[[int], None] | None = None,
) -> ApprovedDryRun:
    """Strictly re-verify the approved dry run against THIS manifest and policy."""
    _require(_sha(approved_result_digest), "--approved-result-digest is not a SHA-256 digest")
    path = Path(path)
    receipt = load_receipt(path)  # strict schema, self-digest, envelope, result digest
    binding = _read_dry_binding(path)
    _require(receipt["binding"] == binding, "receipt binding differs from the directory binding")
    _require(receipt["status"] == "COMPLETE", "dry run is not COMPLETE")
    _require(
        receipt["policy_status"] == WITHIN_GUARDRAILS, "policy status is not within guardrails"
    )
    _require(
        receipt["result_digest"] == approved_result_digest,
        "result digest differs from the approved digest",
    )
    _require(
        binding["cleaning_policy"]
        == {
            "version": policy.version,
            "digest": policy.digest,
            "file_sha256": policy.file_sha256,
            "phase_a_receipt_digest": policy.provenance["phase_a"]["receipt_digest"],
            "candidate_policy_sha256": policy.provenance["candidate_policy"]["sha256"],
        },
        "dry run was made with a different cleaning policy (stale policy)",
    )
    _require(binding["cleaning_policy"]["version"] == POLICY_V2, "dry run policy version")
    _require(
        binding["detector_policy"] == {"version": POLICY_VERSION, "digest": policy_identity()},
        "dry run used different detector semantics",
    )
    _require(
        binding["input_manifest"]
        == {
            "digest": manifest.digest,
            "file_sha256": manifest.file_sha256,
            "kind": manifest.kind,
            "mode": manifest.mode,
            **manifest.totals,
        },
        "dry run was made from a different input manifest (stale dry run)",
    )
    _require(
        binding["data_root"] == manifest.data_root.resolve().as_posix(),
        "dry run read a different data root",
    )
    _require(receipt["source_files"] == source_identities(manifest), "source identities differ")
    _require(
        type(binding["line_ceiling"]) is int and binding["line_ceiling"] > 0,
        "dry run line ceiling",
    )
    raws: dict[str, bytes] = {}
    for name in ARTIFACTS:
        if check is not None:
            check()
        entry = receipt["artifacts"][name]
        raw = read_bounded(path / name, MAX_DRY_ARTIFACT_BYTES, "dry-run artifact")
        _require(
            len(raw) == entry["bytes"]
            and hashlib.sha256(raw).hexdigest() == entry["sha256"]
            and raw.count(b"\n") == entry["records"],
            "an artifact differs from its receipt hash",
        )
        raws[name] = raw
    ruleset = policy.params.ruleset
    expected: dict[int, dict[str, Any]] = {}

    def units() -> Iterator[dict[str, Any]]:
        for item in manifest.files:
            if check is not None:
                check()
            unit = load_unit(path, item, str(binding["digest"]), ruleset)
            merged = CleanStats(ruleset)
            for population in unit["populations"].values():
                merged.merge(CleanStats.from_json(population, ruleset))
            merged.check()
            expected[item.ordinal] = merged.to_json()
            if progress is not None:
                progress(1)
            yield unit

    artifacts, result_digest, status = build_artifacts(
        binding, units(), policy, sorted({f.component for f in manifest.files})
    )
    _require(result_digest == receipt["result_digest"], "re-derived result digest differs")
    _require(status == receipt["policy_status"], "re-derived policy status differs")
    for name in ARTIFACTS:
        _require(artifacts[name] == raws[name], "a published artifact differs from its units")
    accounting = accounting_from_artifacts(
        json.loads(raws["cleaning-dry-run.json"]),
        json.loads(raws["cleaning-by-component.json"]),
        json.loads(raws["cleaning-by-rule.json"]),
    )
    return ApprovedDryRun(path, receipt, binding, expected, accounting)


# -- exact accounting -------------------------------------------------------------------------


def _plain(value: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-normalized mapping (the published artifacts are JSON)."""
    normalized: dict[str, Any] = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    return normalized


def accounting_from_artifacts(
    dry: Mapping[str, Any], by_component: Mapping[str, Any], by_rule: Mapping[str, Any]
) -> dict[str, Any]:
    """The approved accounting, read from the verified dry-run artifacts."""
    try:
        return _plain(
            {
                "global": dry["global"],
                "components": dry["components"],
                "by_component": {
                    name: entry["summary"] for name, entry in by_component["components"].items()
                },
                "rules": {
                    name: {
                        "global": {
                            key: entry["global"][key]
                            for key in ("action", "marginal", "exclusive", "by_class")
                        },
                        "components": entry["components"],
                    }
                    for name, entry in by_rule["rules"].items()
                },
                "guardrails": dry["guardrails"]["status"],
            }
        )
    except (KeyError, TypeError, AttributeError):
        raise ApprovalError("approved dry run refused: artifact accounting schema") from None


def accounting_from_stats(
    global_stats: CleanStats, components: Mapping[str, CleanStats], policy: CompiledPolicy
) -> dict[str, Any]:
    """The same accounting structure, computed from production's own re-evaluation."""
    summaries = {name: summarize(components[name]) for name in sorted(components)}
    global_impacts = rule_impacts(global_stats)
    component_impacts = {name: rule_impacts(components[name]) for name in sorted(components)}
    rules: dict[str, Any] = {}
    for n, name in enumerate(policy.params.ruleset.ids):
        rules[name] = {
            "global": {
                **global_impacts[name],
                "by_class": {
                    cls: impact(
                        int(global_stats.arrays["rule_class"][n, c, 0]),
                        int(global_stats.arrays["rule_class"][n, c, 1]),
                        global_stats,
                    )
                    for c, cls in enumerate(CLASS_ORDER)
                },
            },
            "components": {comp: component_impacts[comp][name] for comp in sorted(components)},
        }
    return _plain(
        {
            "global": summarize(global_stats),
            "components": summaries,
            "by_component": summaries,
            "rules": rules,
            "guardrails": guardrails(global_stats, components, policy)["status"],
        }
    )


def compare_accounting(observed: Mapping[str, Any], approved: Mapping[str, Any]) -> list[str]:
    """Every compared section; refuses (naming the section) on the first difference."""
    for section in ACCOUNTING_SECTIONS:
        if observed.get(section) != approved.get(section):
            raise QualityError(
                f"production decisions differ from the approved dry run ({section}); "
                "fatal: no completion receipt"
            )
    return list(ACCOUNTING_SECTIONS)


def outcome_counts(stats: CleanStats) -> dict[str, int]:
    from xlm.data.quality.cleaning_policy import DROP, KEEP, REVIEW

    outcome = stats.arrays["outcome"]
    return {
        "keep_documents": int(outcome[KEEP, 0]),
        "keep_canonical_bytes": int(outcome[KEEP, 1]),
        "drop_documents": int(outcome[DROP, 0]),
        "drop_canonical_bytes": int(outcome[DROP, 1]),
        "review_documents": int(outcome[REVIEW, 0]),
    }


def components_of(manifest: InputManifest) -> Sequence[str]:
    return sorted({f.component for f in manifest.files})
