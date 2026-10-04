"""Independent verification of a finished Phase-C cleaning, and the cleaned manifest.

``clean-production-verify`` never writes the corpus. In this order it:

1. validates the production receipt strictly and requires it to equal the state
   binding;
2. repeats every pre-cleaning check (frozen policy, manifest, approved dry run
   re-derived from its units, path mapping, root overlaps). The current binding must
   equal the recorded one except for the verifier's own code identity;
3. requires the corpus root to hold EXACTLY the expected outputs: no temporary, no
   unexpected entry, no link or junction;
4. re-derives every artifact from the committed units. They must be byte-identical
   to the published artifacts and reproduce the approved dry run's accounting;
5. re-reads every output file in worker processes. Each must match its SHA-256 and
   size, every row must be strict canonical JSONL with a correct
   ``utf8_byte_count``, the row and canonical byte counts must match, and no output
   row may have the bytes of one of that file's dropped rows. With
   ``--compare-sources`` each source is also re-hashed and walked in lockstep: every
   KEEP source row must be the next output row byte for byte, every DROP row must
   have its recorded SHA-256, and no row may be missing or extra. With
   ``--reevaluate`` every output row is re-decided by the frozen policy and must be
   KEEP;
6. checks the totals against the inventory, the receipt and the approved dry run.

Only then is ``cleaning-production-verification.json`` written to the state output. On
any failure an existing verification record and cleaned manifest are withdrawn.

``clean-production-manifest`` builds the cleaned-corpus input manifest candidate from
a verified inventory only. It has a new kind, a new data root and therefore a new
semantic digest. The candidate is not admitted anywhere: the next stage audits it
independently and reruns C05 from scratch.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import evaluate
from xlm.data.quality.cleaning_policy import KEEP, ComponentRules, RuleParams
from xlm.data.quality.detectors import analyze
from xlm.data.quality.outputs import is_alias
from xlm.data.quality.production import (
    BINDING_FILE,
    CLEANED_MANIFEST_FILE,
    MAX_RECEIPT_BYTES,
    MAX_STATE_BYTES,
    PROGRESS_PREFIX,
    RECEIPT_FILE,
    VERIFICATION_FILE,
    _read_binding,
    check_progress_log,
    load_production_unit,
    prepare_production,
    state_tree,
)
from xlm.data.quality.production_paths import CorpusTree
from xlm.data.quality.production_report import (
    ARTIFACTS,
    CLEANED_INVENTORY,
    DROPPED_MEMBERSHIP,
    build_production_artifacts,
    membership_rows,
    validate_receipt,
)
from xlm.data.quality.progress import Reporter, Telemetry
from xlm.data.quality.runner import Guard
from xlm.data.quality.scan import (
    MANIFEST_KINDS,
    OrderedPool,
    OutputBudget,
    QualityError,
    implementation,
    load_manifest,
    parse_row,
    read_bounded,
)

VERIFICATION_KIND = "xlm_quality_cleaning_production_verification_v1"
CLEANED_KINDS = {"production": "xlm_cleaned_input_manifest", "authored": "authored_cleaned_input"}
CLEANED_STATUS = "CANDIDATE_REQUIRES_INDEPENDENT_AUDIT_AND_C05"
MAX_ARTIFACT_BYTES = 512 * 1024**2
READ_BLOCK = 8 * 1024**2


def load_production_receipt(state: Path) -> dict[str, Any]:
    path = Path(state) / RECEIPT_FILE
    if not path.exists():
        raise QualityError("no production receipt: cleaning is incomplete (never a result)")
    raw = read_bounded(path, MAX_RECEIPT_BYTES, "production receipt")
    try:
        body = canonical.loads_bytes_strict(raw)
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("production receipt invalid: not strict canonical JSON") from None
    if canonical.canonical_bytes(body) != raw:
        raise QualityError("production receipt invalid: not canonical bytes")
    return validate_receipt(body)


def _without_implementation(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in binding.items() if k not in ("implementation", "digest")}


# -- per-file output check (worker) ------------------------------------------------------------


@dataclass(frozen=True)
class OutputCheckTask:
    ordinal: int
    output: str
    file_bytes: int
    sha256: str
    documents: int
    canonical_bytes: int
    line_ceiling: int
    dropped: tuple[tuple[int, str], ...]  # (input row, row SHA-256) of this file's DROP rows
    source: str | None = None
    source_sha256: str = ""
    source_bytes: int = 0
    source_documents: int = 0
    component: str = ""
    rules: ComponentRules | None = None
    params: RuleParams | None = None


@dataclass
class OutputCheckResult:
    ordinal: int
    file_bytes: int
    sha256: str
    documents: int
    canonical_bytes: int
    drop_rows_present: int
    source_compared: bool
    reevaluated: int
    # Operational only.
    nbytes: int = 0
    pid: int = 0
    started: float = 0.0
    finished: float = 0.0
    cpu_seconds: float = 0.0


def _lines(path: str, ceiling: int) -> Iterator[bytes]:
    with open(path, "rb", buffering=READ_BLOCK) as stream:
        while line := stream.readline(ceiling + 1):
            if len(line) > ceiling:
                raise QualityError("a row exceeds the document ceiling")
            yield line


def check_output_file(task: OutputCheckTask) -> OutputCheckResult:
    """Re-read one cleaned output (and optionally its source) without writing anything."""
    started, cpu = time.monotonic(), time.process_time()
    dropped = dict(task.dropped)
    dropped_bytes = set(dropped.values())
    digest = hashlib.sha256()
    size = rows = text_bytes = hits = reevaluated = 0
    source: Iterator[bytes] | None = None
    source_digest = hashlib.sha256()
    source_size = source_row = 0
    if task.source is not None:
        source = _lines(task.source, task.line_ceiling)

    def next_kept_source_row() -> bytes | None:
        """Advance the source past DROP rows (each must hold its recorded bytes)."""
        nonlocal source_size, source_row
        assert source is not None
        for raw in source:
            source_digest.update(raw)
            source_size += len(raw)
            source_row += 1
            if source_row in dropped:
                body = raw[:-1] if raw.endswith(b"\n") else raw
                if hashlib.sha256(body).hexdigest() != dropped[source_row]:
                    raise QualityError("a dropped source row differs from its membership digest")
                continue
            return raw
        return None

    for line in _lines(task.output, task.line_ceiling):
        digest.update(line)
        size += len(line)
        rows += 1
        body = line[:-1] if line.endswith(b"\n") else line
        if hashlib.sha256(body).hexdigest() in dropped_bytes:
            hits += 1
        document = parse_row(body)  # strict canonical JSON, exact CanonicalDocument fields
        text = document["text"]
        try:
            nbytes = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise QualityError("cleaned text is not valid Unicode scalar values") from None
        if nbytes != document["utf8_byte_count"]:
            raise QualityError("cleaned row utf8_byte_count disagrees with its text")
        text_bytes += nbytes
        if task.params is not None and task.rules is not None:
            result = analyze(text, nbytes)
            decision = evaluate(
                result.values, frozenset(result.flags), result.doc_class, task.rules, task.params
            )
            if decision.outcome != KEEP:
                raise QualityError("a cleaned output row is not KEEP under the frozen policy")
            reevaluated += 1
        if source is not None and next_kept_source_row() != line:
            raise QualityError("a cleaned row is not the next KEEP source row byte for byte")
    if source is not None:
        if next_kept_source_row() is not None:
            raise QualityError("a KEEP source row is missing from the cleaned output")
        if (source_size, source_digest.hexdigest(), source_row) != (
            task.source_bytes,
            task.source_sha256,
            task.source_documents,
        ):
            raise QualityError("source file differs from the frozen manifest SHA-256/size/rows")
    return OutputCheckResult(
        ordinal=task.ordinal,
        file_bytes=size,
        sha256=digest.hexdigest(),
        documents=rows,
        canonical_bytes=text_bytes,
        drop_rows_present=hits,
        source_compared=source is not None,
        reevaluated=reevaluated,
        nbytes=size + source_size,
        pid=os.getpid(),
        started=started,
        finished=time.monotonic(),
        cpu_seconds=time.process_time() - cpu,
    )


# -- verification ---------------------------------------------------------------------------


def _withdraw(state: Path, names: tuple[str, ...]) -> None:
    """Remove owned verification-dependent records (regular files only)."""
    if not (state / BINDING_FILE).is_file():
        return
    for name in names:
        path = state / name
        if path.exists() and not is_alias(path) and path.is_file():
            path.unlink()


def verify_production(
    manifest_path: Path,
    policy_path: Path,
    approved_dry_run: Path,
    output_root: Path,
    state_output: Path,
    *,
    approved_result_digest: str,
    data_root: Path | None = None,
    workers: int = 4,
    compare_sources: bool = False,
    reevaluate: bool = False,
    max_rss_bytes: int = 8 * 1024**3,
    deadline_seconds: float = 12 * 3600.0,
    progress_interval: float | None = 5.0,
    progress_log: Path | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    """Re-read and re-hash the finished cleaned corpus; write the verification record."""
    started = time.monotonic() if started is None else started
    state = Path(state_output)
    output_root = Path(output_root)
    if progress_log is not None:
        check_progress_log(
            progress_log,
            manifest_path,
            policy_path,
            approved_dry_run,
            data_root,
            output_root,
            state,
        )
    telemetry = Telemetry(workers, started)
    reporter = Reporter(
        telemetry,
        5.0 if progress_interval is None else progress_interval,
        stderr=progress_interval is not None,
        log=progress_log,
        prefix=PROGRESS_PREFIX.replace("]", "-verify]"),
    )
    receipt_valid = False
    try:
        with reporter:
            with Guard(
                deadline_seconds=deadline_seconds,
                started=started,
                max_rss_bytes=max_rss_bytes,
                watch=[state],
                reserve_bytes=0,
            ) as guard:
                receipt = load_production_receipt(state)
                receipt_valid = True
                result = _verify(
                    receipt,
                    manifest_path,
                    policy_path,
                    approved_dry_run,
                    output_root,
                    state,
                    approved_result_digest=approved_result_digest,
                    data_root=data_root,
                    workers=workers,
                    compare_sources=compare_sources,
                    reevaluate=reevaluate,
                    guard=guard,
                    telemetry=telemetry,
                )
            guard.final(0.0)
    except BaseException:
        if receipt_valid:
            with contextlib.suppress(OSError):
                _withdraw(state, (VERIFICATION_FILE, CLEANED_MANIFEST_FILE))
        raise
    return result


def _verify(
    receipt: Mapping[str, Any],
    manifest_path: Path,
    policy_path: Path,
    approved_dry_run: Path,
    output_root: Path,
    state: Path,
    *,
    approved_result_digest: str,
    data_root: Path | None,
    workers: int,
    compare_sources: bool,
    reevaluate: bool,
    guard: Guard,
    telemetry: Telemetry,
) -> dict[str, Any]:
    recorded = _read_binding(state)
    if receipt["binding"] != recorded:
        raise QualityError("receipt binding differs from the state binding")
    setup = prepare_production(
        manifest_path,
        policy_path,
        approved_dry_run,
        output_root,
        state,
        approved_result_digest=approved_result_digest,
        data_root=data_root,
        check=guard.check,
        telemetry=telemetry,
    )
    if _without_implementation(setup.binding) != _without_implementation(recorded):
        raise QualityError(
            "current manifest/policy/dry run/output root/mapping differ from the production binding"
        )
    manifest, policy, approved = setup.manifest, setup.policy, setup.approved
    budget = OutputBudget(MAX_STATE_BYTES)
    tree = state_tree(state, setup.state_protected, budget)
    tree.open(create=False)
    budget.used = tree.used_bytes()
    corpus = CorpusTree(output_root, setup.mapping, setup.corpus_protected)
    finals, temps = corpus.open(create=False, fresh=False)
    if temps:
        raise QualityError("an interrupted temporary output is present in the cleaned corpus")
    if finals != set(corpus.expected):
        raise QualityError("the cleaned corpus does not hold exactly the expected outputs")
    raws: dict[str, bytes] = {}
    for name in ARTIFACTS:
        entry = receipt["artifacts"][name]
        raw = read_bounded(state / name, MAX_ARTIFACT_BYTES, "production artifact")
        if (len(raw), hashlib.sha256(raw).hexdigest(), raw.count(b"\n")) != (
            entry["bytes"],
            entry["sha256"],
            entry["records"],
        ):
            raise QualityError("a production artifact differs from its receipt hash")
        raws[name] = raw
    mapped = {m.ordinal: m for m in setup.mapping}
    units: dict[int, dict[str, Any]] = {}
    telemetry.set_phase("aggregate", len(manifest.files), "units")

    def stream() -> Iterator[dict[str, Any]]:
        for item in manifest.files:
            guard.check()
            unit = load_production_unit(
                state, item, mapped[item.ordinal], recorded, policy, approved
            )
            units[item.ordinal] = {
                "output": unit["output"],
                "dropped": tuple((r["row"], r["row_sha256"]) for r in unit["dropped"]),
            }
            telemetry.advance(1)
            yield unit

    artifacts, result_digest, built = build_production_artifacts(
        recorded, stream(), policy, approved
    )
    if result_digest != receipt["result_digest"]:
        raise QualityError("re-derived result digest differs from the receipt")
    for name in ARTIFACTS:
        if artifacts[name] != raws[name]:
            raise QualityError("a production artifact differs from its re-derivation")
    if built["outputs"] != receipt["output_files"] or built["accounting"] != receipt["accounting"]:
        raise QualityError("receipt outputs or accounting differ from the committed units")
    membership = membership_rows(raws[DROPPED_MEMBERSHIP])
    inventory = json.loads(raws[CLEANED_INVENTORY])
    if [f["output"] for f in inventory["files"]] != [
        {**units[f.ordinal]["output"]} for f in manifest.files
    ]:
        raise QualityError("inventory outputs differ from the committed units")
    for entry, mapped_file in zip(inventory["files"], setup.mapping, strict=True):
        if (entry["input"]["path"], entry["output"]["path"]) != (
            mapped_file.input_path,
            mapped_file.output_path,
        ):
            raise QualityError("inventory mapping differs from the deterministic mapping")
    # Independent re-read of every output (and optionally every source) in workers.
    root = manifest.data_root
    tasks = [
        OutputCheckTask(
            ordinal=item.ordinal,
            output=str(corpus.final(mapped[item.ordinal].output_path)),
            file_bytes=int(units[item.ordinal]["output"]["file_bytes"]),
            sha256=str(units[item.ordinal]["output"]["sha256"]),
            documents=int(units[item.ordinal]["output"]["documents"]),
            canonical_bytes=int(units[item.ordinal]["output"]["canonical_bytes"]),
            line_ceiling=approved.line_ceiling,
            dropped=units[item.ordinal]["dropped"],
            source=str(root / item.path) if compare_sources else None,
            source_sha256=item.documents_sha256,
            source_bytes=item.file_bytes,
            source_documents=item.documents,
            component=item.component,
            rules=policy.rules_for(item.component) if reevaluate else None,
            params=policy.params if reevaluate else None,
        )
        for item in manifest.files
    ]
    total = sum(t.file_bytes for t in tasks) + (
        sum(f.file_bytes for f in manifest.files) if compare_sources else 0
    )
    telemetry.set_phase("verify-output", total, "bytes")
    checked = documents = text_bytes = file_bytes = reevaluated = 0
    with OrderedPool(workers, guard, telemetry) as pool:
        for task, result in zip(tasks, pool.map(check_output_file, tasks), strict=True):
            telemetry.measured(
                result.documents,
                result.nbytes,
                result.finished - result.started,
                result.cpu_seconds,
                result.pid,
            )
            telemetry.committed(0, 0)
            if (result.file_bytes, result.sha256) != (task.file_bytes, task.sha256):
                raise QualityError(f"cleaned output {task.ordinal} SHA-256 or size differs")
            if (result.documents, result.canonical_bytes) != (task.documents, task.canonical_bytes):
                raise QualityError(f"cleaned output {task.ordinal} row or byte count differs")
            if result.drop_rows_present:
                raise QualityError(f"cleaned output {task.ordinal} holds a dropped row")
            checked += 1
            documents += result.documents
            text_bytes += result.canonical_bytes
            file_bytes += result.file_bytes
            reevaluated += result.reevaluated
    # Totals: inventory, receipt and the approved dry run's KEEP/DROP accounting.
    totals = inventory["totals"]
    approved_global = approved.accounting["global"]["outcomes"]
    if (
        documents != totals["output"]["documents"] == approved_global["KEEP"]["docs"]
        or text_bytes != totals["output"]["canonical_bytes"] == approved_global["KEEP"]["bytes"]
        or file_bytes != totals["output"]["file_bytes"]
        or len(membership) != approved_global["DROP"]["docs"]
        or sum(r["canonical_bytes"] for r in membership) != approved_global["DROP"]["bytes"]
    ):
        raise QualityError("verified totals differ from the approved dry-run accounting")
    for name, counts in inventory["components"].items():
        outcome = approved.accounting["components"][name]["outcomes"]
        if (counts["keep"]["documents"], counts["keep"]["canonical_bytes"]) != (
            outcome["KEEP"]["docs"],
            outcome["KEEP"]["bytes"],
        ) or (counts["drop"]["documents"], counts["drop"]["canonical_bytes"]) != (
            outcome["DROP"]["docs"],
            outcome["DROP"]["bytes"],
        ):
            raise QualityError("verified component totals differ from the approved dry run")
    identity = implementation()
    record: dict[str, Any] = {
        "kind": VERIFICATION_KIND,
        "status": "VERIFIED",
        "receipt_digest": receipt["digest"],
        "binding_digest": recorded["digest"],
        "result_digest": receipt["result_digest"],
        "cleaned_inventory_sha256": receipt["cleaned_inventory_sha256"],
        "dropped_membership_sha256": receipt["dropped_membership_sha256"],
        "output_root": recorded["output_root"],
        "files_verified": checked,
        "documents": documents,
        "canonical_bytes": text_bytes,
        "file_bytes": file_bytes,
        "dropped_rows": len(membership),
        "checks": {
            "exact_output_set": True,
            "output_sha256_and_size": True,
            "strict_canonical_rows": True,
            "dropped_row_bytes_absent": True,
            "deterministic_mapping": True,
            "artifacts_rederived": True,
            "approved_dry_run_accounting": "IDENTICAL",
            "sources_compared_byte_for_byte": compare_sources,
            "rows_reevaluated_keep": reevaluated if reevaluate else None,
        },
        "verifier_implementation": {
            "code_identity": identity["code_identity"],
            "dependency_sha256": identity["dependency_sha256"],
        },
    }
    record["digest"] = canonical.digest(record)
    guard.check()
    tree.write(VERIFICATION_FILE, canonical.canonical_bytes(record))
    return {
        "verified": True,
        "verification_digest": record["digest"],
        "receipt_digest": receipt["digest"],
        "result_digest": receipt["result_digest"],
        "files_verified": checked,
        "documents": documents,
        "canonical_bytes": text_bytes,
        "file_bytes": file_bytes,
        "dropped_rows": len(membership),
        "sources_compared": compare_sources,
        "rows_reevaluated": reevaluated if reevaluate else None,
        "corpus_modified": False,
    }


def load_verification(state: Path, receipt: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(state) / VERIFICATION_FILE
    if not path.exists():
        raise QualityError("no verification record: run `clean-production-verify` first")
    try:
        body = canonical.loads_bytes_strict(read_bounded(path, 1024**2, "verification record"))
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("verification record is not strict canonical JSON") from None
    if (
        not isinstance(body, dict)
        or body.get("kind") != VERIFICATION_KIND
        or body.get("status") != "VERIFIED"
        or body.get("digest") != canonical.self_digest(body)
        or body.get("receipt_digest") != receipt["digest"]
        or body.get("result_digest") != receipt["result_digest"]
        or body.get("cleaned_inventory_sha256") != receipt["cleaned_inventory_sha256"]
        or body.get("dropped_membership_sha256") != receipt["dropped_membership_sha256"]
        or body.get("output_root") != receipt["output_root"]
    ):
        raise QualityError("verification record does not verify this production receipt")
    return body


# -- cleaned manifest ---------------------------------------------------------------------------


def build_cleaned_manifest(state_output: Path, output_root: Path) -> dict[str, Any]:
    """Cleaned-corpus input manifest candidate from a VERIFIED inventory (new digest)."""
    state = Path(state_output)
    receipt = load_production_receipt(state)
    binding = _read_binding(state)
    if receipt["binding"] != binding:
        raise QualityError("receipt binding differs from the state binding")
    verification = load_verification(state, receipt)
    if Path(output_root).resolve().as_posix() != binding["output_root"]:
        raise QualityError("--output-root is not the verified cleaned corpus root")
    entry = receipt["artifacts"][CLEANED_INVENTORY]
    raw = read_bounded(state / CLEANED_INVENTORY, MAX_ARTIFACT_BYTES, "cleaned inventory")
    if (len(raw), hashlib.sha256(raw).hexdigest()) != (entry["bytes"], entry["sha256"]):
        raise QualityError("cleaned inventory differs from its receipt hash")
    inventory = json.loads(raw)
    root = Path(output_root)
    for item in inventory["files"]:
        path = root.joinpath(*item["output"]["path"].split("/"))
        if (
            is_alias(path)
            or not path.is_file()
            or path.stat().st_size != item["output"]["file_bytes"]
        ):
            raise QualityError("a verified output is missing or changed; re-run verification")
    mode = binding["input_manifest"]["mode"]
    kind = CLEANED_KINDS[mode]
    files = [
        {
            "path": item["output"]["path"],
            "source_key": item["source_key"],
            "component": item["component"],
            "view": item["view"],
            "upstream_component": item["upstream_component"],
            "documents_sha256": item["output"]["sha256"],
            "file_bytes": item["output"]["file_bytes"],
            "canonical_bytes": item["output"]["canonical_bytes"],
            "documents": item["output"]["documents"],
            "cleaned_from": item["input"],
        }
        for item in inventory["files"]
    ]
    fields = ("documents", "canonical_bytes", "file_bytes")
    components = {
        name: {k: sum(int(f[k]) for f in files if f["component"] == name) for k in fields}
        for name in sorted({f["component"] for f in files})
    }
    body: dict[str, Any] = {
        "kind": kind,
        "version": 1,
        "status": CLEANED_STATUS,
        "data_root": binding["output_root"],
        "files": files,
        "components": components,
        "totals": {k: sum(int(f[k]) for f in files) for k in fields},
        "empty_files": sum(1 for f in files if f["documents"] == 0),
        "lineage": {
            "original_input_manifest_digest": binding["input_manifest"]["digest"],
            "original_input_manifest_file_sha256": binding["input_manifest"]["file_sha256"],
            "original_input_manifest_kind": binding["input_manifest"]["kind"],
            "cleaning_policy": dict(binding["cleaning_policy"]),
            "approved_dry_run_receipt_digest": binding["approved_dry_run"]["receipt_digest"],
            "approved_dry_run_result_digest": binding["approved_dry_run"]["result_digest"],
            "production_receipt_digest": receipt["digest"],
            "production_result_digest": receipt["result_digest"],
            "verification_digest": verification["digest"],
            "cleaned_inventory_sha256": receipt["cleaned_inventory_sha256"],
            "dropped_membership_sha256": receipt["dropped_membership_sha256"],
        },
        "training_permitted": False,
        "document_ids_materialized": False,
        "requires_scan_time_hash_verification": True,
        "c05": "NOT RUN for this corpus; every earlier C05 artifact is stale",
    }
    body["digest"] = canonical.self_digest(body)
    if kind not in MANIFEST_KINDS or body["digest"] == binding["input_manifest"]["digest"]:
        raise QualityError("cleaned manifest would reuse the original manifest identity")
    payload = canonical.canonical_bytes(body)
    target = state / CLEANED_MANIFEST_FILE
    if target.exists():
        if is_alias(target) or read_bounded(target, MAX_ARTIFACT_BYTES, "manifest") != payload:
            raise QualityError("a different cleaned manifest already exists; nothing overwritten")
    else:
        budget = OutputBudget(MAX_STATE_BYTES)
        tree = state_tree(state, [root], budget)
        tree.open(create=False)
        budget.used = tree.used_bytes()
        tree.write(CLEANED_MANIFEST_FILE, payload)
    loaded = load_manifest(target)  # the candidate must load as a quality/C05 input manifest
    if loaded.digest != body["digest"] or loaded.totals != {
        "files": len(files),
        **body["totals"],
    }:
        raise QualityError("cleaned manifest does not reload to its own identity")
    return {
        "cleaned_manifest": str(target),
        "digest": body["digest"],
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "kind": kind,
        "status": CLEANED_STATUS,
        "data_root": body["data_root"],
        "totals": body["totals"],
        "files": len(files),
        "original_input_manifest_digest": binding["input_manifest"]["digest"],
        "next": "independent audit of this manifest, then C05 from scratch (operator)",
    }
