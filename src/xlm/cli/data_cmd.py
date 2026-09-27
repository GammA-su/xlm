"""CLI commands for data import and processing adhering to C02, C03, and C04."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from xlm.artifacts.store import ArtifactStore
from xlm.core.contracts import CanonicalDocument
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    AcquisitionVerifier,
    BoundedFetcher,
    PlanAuthorization,
    ProgressJournal,
    SamplingFrame,
    load_acquisition_plan,
    save_acquisition_plan,
)
from xlm.data.acquisition.fetcher import CONTENT_RANGE_RE
from xlm.data.acquisition.plan import ParquetWindowDecode, plan_requires_production_admission
from xlm.data.acquisition.sampling import (
    FileLayout,
    SamplingRefusal,
    SamplingRequest,
    canonical_range_url,
    discover_layout_local,
    discover_layout_over_ranges,
    plan_sample_blocks,
)
from xlm.data.adapters.jsonl import JsonlAdapter
from xlm.data.adapters.text import TextAdapter
from xlm.data.canonical_io import (
    HAS_PYARROW,
    CanonicalDatasetReader,
    CanonicalDatasetWriter,
    create_dataset_manifest,
)
from xlm.data.cleaning import (
    PipelineExecutionSummary,
    QualityReporter,
    QuarantineManager,
    QuarantinePolicy,
    StageStats,
    create_pipeline_preset,
    run_sharded_clean,
)
from xlm.data.dedup import (
    DedupConfig,
    DedupResult,
    DedupStats,
    DuplicateCluster,
    MinHashConfig,
    run_sharded_dedup,
)
from xlm.data.normalization import compute_sha256
from xlm.data.pools import (
    DiagnosticCorpusFreeze,
    FrozenPoolManifest,
    LeakDetectedError,
    OverlapPolicy,
    PoolBinding,
    PoolBuildConfig,
    PoolPublicationError,
    SourceView,
    SplitConfig,
    TokenizerFitConfig,
    apply_splits,
    assign_splits,
    build_pool,
    build_regime,
    build_split_groups,
    build_tokenizer_fit_manifest,
    freeze_pool,
    restrict_membership_to_split,
    tokenizer_fit_resource_plan,
    verify_group_disjointness,
    verify_pool_offline,
)
from xlm.data.sources.admission import (
    AdmissionDecision,
    AdmissionGate,
    CatalogAuditor,
    load_probe_evidence,
    save_admission_decision,
    save_probe_evidence,
)
from xlm.data.sources.catalog import load_catalog
from xlm.data.sources.policy import (
    LEGAL_DISCLAIMER,
    is_denied_source,
)
from xlm.data.sources.prober import EvidenceType, SourceProber
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    DiscoveryTransport,
    HostNotAllowlistedError,
    HttpsManifestTransport,
    HuggingFaceTransport,
    LocalManifestTransport,
    SafeRedirectHandler,
    TransportBudget,
    validate_host,
)
from xlm.data.tokens import TokenShardWriter

app = typer.Typer(help="Data import, inspection, and canonicalization commands.")


@app.command("import-local")
def import_local(
    manifest_path: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            "-m",
            help="Path to YAML source manifest describing files and split assignments.",
        ),
    ] = None,
    input_path: Annotated[
        Path | None,
        typer.Option("--input", "-i", help="Direct path to source text or jsonl file."),
    ] = None,
    source_id: Annotated[
        str | None,
        typer.Option("--source-id", "-s", help="Unique identifier of data source."),
    ] = None,
    format_type: Annotated[
        str,
        typer.Option("--format", "-f", help="Format of source files ('text' or 'jsonl')."),
    ] = "jsonl",
    split: Annotated[
        str,
        typer.Option(
            "--split", help="Target split for single file ('train', 'diagnostic_val', 'audit')."
        ),
    ] = "train",
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir", "-o", help="Directory where canonical data should be written."
        ),
    ] = None,
    publish: Annotated[
        bool,
        typer.Option("--publish/--no-publish", help="Publish dataset as an immutable artifact."),
    ] = False,
) -> None:
    """Import local raw text or JSONL files into canonical document records."""
    docs: list[CanonicalDocument] = []
    actual_source_id = source_id or "local_source"
    source_revision = "local_snapshot"
    license_ref = "unknown"

    if manifest_path is not None:
        if not manifest_path.is_file():
            typer.echo(f"Error: Manifest file not found: {manifest_path}", err=True)
            raise typer.Exit(code=1)

        try:
            manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            typer.echo(f"Error: Failed to parse manifest YAML: {e}", err=True)
            raise typer.Exit(code=1) from e

        actual_source_id = manifest_data.get("source_id", actual_source_id)
        source_revision = manifest_data.get("source_revision", source_revision)
        license_ref = manifest_data.get("license", license_ref)
        manifest_dir = manifest_path.parent

        files_info = manifest_data.get("files", [])
        splits_cfg = manifest_data.get("splits", {})

        # Build split lookup map from manifest
        split_map: dict[str, str] = {}
        for sp_name, doc_id_list in splits_cfg.items():
            for d_id in doc_id_list:
                split_map[d_id] = sp_name

        for f_entry in files_info:
            rel_p = f_entry.get("path")
            f_fmt = f_entry.get("format", "jsonl")
            full_p = manifest_dir / rel_p

            if f_fmt == "jsonl":
                adapter = JsonlAdapter(
                    source_id=actual_source_id,
                    source_revision=source_revision,
                    license_reference=license_ref,
                )
                try:
                    for doc in adapter.process_file(full_p, default_split=split):
                        # Override split if declared in split_map
                        if doc.doc_id in split_map:
                            doc = CanonicalDocument(
                                **{**doc.to_dict(), "split": split_map[doc.doc_id]}
                            )
                        docs.append(doc)
                except Exception as e:
                    typer.echo(f"Error processing JSONL file {full_p}: {e}", err=True)
                    raise typer.Exit(code=1) from e
            elif f_fmt == "text":
                txt_adapter = TextAdapter(
                    source_id=actual_source_id,
                    source_revision=source_revision,
                    license_reference=license_ref,
                )
                try:
                    doc = txt_adapter.process_file(
                        full_p,
                        split=split_map.get(full_p.stem, split),
                    )
                    docs.append(doc)
                except Exception as e:
                    typer.echo(f"Error processing text file {full_p}: {e}", err=True)
                    raise typer.Exit(code=1) from e

    elif input_path is not None:
        if not input_path.is_file():
            typer.echo(f"Error: Input file not found: {input_path}", err=True)
            raise typer.Exit(code=1)

        if format_type == "jsonl":
            adapter = JsonlAdapter(
                source_id=actual_source_id,
                source_revision=source_revision,
                license_reference=license_ref,
            )
            try:
                docs.extend(adapter.process_file(input_path, default_split=split))
            except Exception as e:
                typer.echo(f"Error processing JSONL file {input_path}: {e}", err=True)
                raise typer.Exit(code=1) from e
        elif format_type == "text":
            txt_adapter = TextAdapter(
                source_id=actual_source_id,
                source_revision=source_revision,
                license_reference=license_ref,
            )
            try:
                docs.append(txt_adapter.process_file(input_path, split=split))
            except Exception as e:
                typer.echo(f"Error processing text file {input_path}: {e}", err=True)
                raise typer.Exit(code=1) from e
    else:
        typer.echo("Error: Either --manifest or --input must be specified.", err=True)
        raise typer.Exit(code=1)

    if not docs:
        typer.echo("Error: No documents were imported.", err=True)
        raise typer.Exit(code=1)

    # Determine destination directory
    effective_out_dir = output_dir
    if effective_out_dir is None and not publish:
        effective_out_dir = Path("scratch") / f"canonical_{actual_source_id}"

    # Write files
    staging_dir = effective_out_dir or (Path(".staging") / f"dataset_{actual_source_id}")
    staging_dir.mkdir(parents=True, exist_ok=True)

    writer = CanonicalDatasetWriter(staging_dir)
    jsonl_path = writer.write_jsonl(docs)
    summary = create_dataset_manifest(docs, actual_source_id, source_revision)

    try:
        writer.write_parquet(docs)
    except Exception:
        pass

    typer.echo(f"Imported {len(docs)} documents from source '{actual_source_id}'.")
    typer.echo(f"Total canonical UTF-8 bytes: {summary['total_utf8_bytes']:,}")
    typer.echo(f"Split breakdown: {summary['split_counts']}")
    typer.echo(f"Written to: {jsonl_path}")

    if publish:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        artifact_id = f"canonical_{actual_source_id}"
        meta = {
            "source_id": actual_source_id,
            "source_revision": source_revision,
            "num_documents": summary["num_documents"],
            "total_utf8_bytes": summary["total_utf8_bytes"],
            "split_counts": summary["split_counts"],
            "content_hash": summary["content_hash"],
        }
        files_map: dict[str, Path] = {"documents.jsonl": jsonl_path}
        parquet_candidate = staging_dir / "documents.parquet"
        if parquet_candidate.is_file():
            files_map["documents.parquet"] = parquet_candidate

        published_dir = store.publish_artifact(
            artifact_id=artifact_id,
            kind="clean",
            files=files_map,
            producer_code_hash=compute_sha256("xlm.cli.data_cmd:import_local")[:16],
            dependency_hash=compute_sha256("uv.lock")[:16],
            resolved_config_hash=summary["content_hash"],
            metadata=meta,
        )
        typer.echo(f"Published immutable artifact to: {published_dir}")


@app.command("sources")
def list_sources(
    catalog_path: Annotated[
        Path,
        typer.Option(
            "--catalog",
            "-c",
            help="Path to dataset catalog YAML.",
        ),
    ] = Path("manifests/datasets.catalog.yaml"),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output catalog as formatted JSON."),
    ] = False,
) -> None:
    """List discovery candidate sources from the dataset catalog adhering to Contract C04."""
    try:
        catalog = load_catalog(catalog_path)
    except Exception as e:
        typer.echo(f"Error loading catalog: {e}", err=True)
        raise typer.Exit(code=1) from e

    if as_json:
        typer.echo(json.dumps(catalog.model_dump(), indent=2))
        return

    typer.echo("=" * 80)
    typer.echo(f"XLM Dataset Discovery Catalog: {catalog.catalog_id} (Status: {catalog.status})")
    typer.echo(f"Deny Direct Sources: {catalog.deny_direct_sources}")
    typer.echo(f"Silent Fallback Allowed: {catalog.silent_fallback_allowed}")
    typer.echo("=" * 80)
    typer.echo(f"{'#':<3} {'Source ID':<22} {'Provider':<12} {'Repository':<32} {'Approval':<10}")
    typer.echo("-" * 80)
    for src in catalog.sources:
        approval_str = "Approved" if src.operator_approved else "Pending"
        row_str = (
            f"{src.candidate_number:<3} {src.source_id:<22} "
            f"{src.provider:<12} {src.repository:<32} {approval_str:<10}"
        )
        typer.echo(row_str)
    typer.echo("-" * 80)
    typer.echo(f"Total candidate sources: {len(catalog.sources)}")
    typer.echo("Note: Discovery candidates are unadmitted. Probing and operator approval required.")


@app.command("probe")
def probe_source(
    source_id: Annotated[
        str,
        typer.Option("--source", "-s", help="Source ID from catalog to probe."),
    ],
    view_id: Annotated[
        str,
        typer.Option("--view", "-v", help="View / subset identifier to probe."),
    ] = "default",
    catalog_path: Annotated[
        Path,
        typer.Option("--catalog", "-c", help="Path to dataset catalog YAML."),
    ] = Path("manifests/datasets.catalog.yaml"),
    live: Annotated[
        bool,
        typer.Option("--live/--offline", help="Allow bounded live network discovery probing."),
    ] = False,
    budget_mib: Annotated[
        float,
        typer.Option("--budget-mib", help="Transferred bytes ceiling in MiB (max 32)."),
    ] = 32.0,
    publish: Annotated[
        bool,
        typer.Option(
            "--publish/--no-publish", help="Persist probe evidence as an immutable P01 artifact."
        ),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output evidence record as JSON."),
    ] = False,
    probe_id: Annotated[
        str,
        typer.Option(
            "--probe-id",
            help="Explicit discovery attempt identity; reruns share its spent allowance.",
        ),
    ] = "default",
) -> None:
    """Inspect and probe a candidate source snapshot within bounded discovery limits."""
    try:
        catalog = load_catalog(catalog_path)
    except Exception as e:
        typer.echo(f"Error loading catalog: {e}", err=True)
        raise typer.Exit(code=1) from e

    candidate = catalog.get_source(source_id)
    if candidate is None:
        typer.echo(f"Error: Source '{source_id}' not found in catalog {catalog_path}.", err=True)
        raise typer.Exit(code=1)

    # Check direct denial policy first
    if is_denied_source(candidate.repository):
        typer.echo(
            f"Error: Source '{source_id}' ({candidate.repository}) is DENIED by policy C04/A13.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not 0 < budget_mib <= 32:
        raise ValueError("discovery --budget-mib must be greater than zero and at most 32")
    budget = TransportBudget(max_bytes=int(budget_mib * 1024 * 1024))
    transport: DiscoveryTransport

    if live:
        import hashlib

        from xlm.artifacts.manifest import validate_component
        from xlm.data.acquisition.disk import StorageCapacityManager

        validate_component(probe_id)
        identity = hashlib.sha256(
            json.dumps(
                {
                    "candidate": candidate.model_dump(),
                    "view": view_id,
                    "budget_bytes": budget.max_bytes,
                    "probe_id": probe_id,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        paths = ArtifactPaths.from_env()
        journal = ProgressJournal(
            paths.root / "discovery" / f"{source_id}_{view_id}_{probe_id}.json",
            f"probe_{source_id}_{view_id}_{probe_id}",
            identity,
        )
        capacity = StorageCapacityManager(
            budget.max_bytes,
            budget.max_decompressed_bytes,
            16 * 1024**2,
            16 * 1024**2,
            journal=journal,
        )
        capacity.bind_deadline(budget.deadline_seconds)
        budget.capacity = capacity
        budget.bytes_transferred = journal.state.transferred_bytes
        budget.requests_made = journal.state.requests_made
        if candidate.provider == "huggingface":
            transport = HuggingFaceTransport(budget)
        elif candidate.provider == "https":
            transport = HttpsManifestTransport(budget)
        elif candidate.provider == "local":
            transport = LocalManifestTransport([Path.cwd()])
        else:
            typer.echo(
                f"Error: Unsupported provider '{candidate.provider}' for live probe.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        # Offline probing: check local manifest or artifact store
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        existing_evidence = load_probe_evidence(candidate.source_id, view_id, store)
        if existing_evidence is not None:
            typer.echo(
                f"Found saved probe evidence in artifact store for {source_id}:{view_id}.",
                err=as_json,
            )
            if as_json:
                typer.echo(json.dumps(existing_evidence.to_canonical_dict(), indent=2))
            else:
                typer.echo(f"Outcome: {existing_evidence.outcome.value}")
                typer.echo(f"Immutable revision: {existing_evidence.immutable_revision}")
                typer.echo(f"Fingerprint: {existing_evidence.probe_fingerprint}")
            return

        if candidate.provider == "local":
            transport = LocalManifestTransport([Path.cwd()])
        else:
            typer.echo(
                f"Error: Offline probe for '{candidate.provider}' requires existing "
                "probe evidence in artifact store. Use --live for bounded network discovery.",
                err=True,
            )
            raise typer.Exit(code=1)

    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=budget,
        view_id=view_id,
        evidence_type=EvidenceType.REAL_OBSERVED if live else EvidenceType.SYNTHETIC_FIXTURE,
    )
    evidence = prober.probe()

    if publish:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        try:
            art_dir = save_probe_evidence(evidence, store)
            typer.echo(f"Persisted probe evidence artifact to: {art_dir}", err=as_json)
        except Exception as e:
            typer.echo(f"Error: Could not persist probe evidence artifact: {e}", err=True)
            raise typer.Exit(code=1) from e

    if as_json:
        typer.echo(json.dumps(evidence.to_canonical_dict(), indent=2))
    else:
        typer.echo("=" * 60)
        typer.echo(f"Discovery Probe Evidence: {source_id} (view: {view_id})")
        typer.echo(f"Outcome: {evidence.outcome.value}")
        typer.echo(f"Immutable Revision: {evidence.immutable_revision or 'UNRESOLVED'}")
        typer.echo(f"Fingerprint: {evidence.probe_fingerprint or 'NONE'}")
        typer.echo(f"Observed Files: {evidence.observed_files_count}")
        typer.echo(f"Declared License: {evidence.declared_license or 'Unknown'}")
        typer.echo(f"Resource Metrics: {evidence.resource_metrics}")
        if evidence.unresolved_requirements:
            typer.echo(f"Unresolved Requirements: {evidence.unresolved_requirements}")
        if evidence.reason:
            typer.echo(f"Reason: {evidence.reason}")
        typer.echo("=" * 60)


@app.command("audit")
def audit_catalog(
    catalog_path: Annotated[
        Path,
        typer.Option("--catalog", "-c", help="Path to dataset catalog YAML."),
    ] = Path("manifests/datasets.catalog.yaml"),
    source_id: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Optional single source ID to audit."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict", help="Fail with exit code 1 if any candidate is unadmitted or blocked."
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output audit report as JSON."),
    ] = False,
) -> None:
    """Audit discovery candidate catalog against admission gates adhering to C04."""
    try:
        catalog = load_catalog(catalog_path)
    except Exception as e:
        typer.echo(f"Error loading catalog: {e}", err=True)
        raise typer.Exit(code=1) from e

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    auditor = CatalogAuditor(catalog=catalog, artifact_store=store)

    if source_id:
        cand = catalog.get_source(source_id)
        if not cand:
            typer.echo(f"Error: Source '{source_id}' not found in catalog.", err=True)
            raise typer.Exit(code=1)
        res = auditor.audit_source(cand)
        if as_json:
            typer.echo(json.dumps(res.model_dump(), indent=2))
        else:
            typer.echo(
                f"Source: {source_id} | Status: {res.status.value} | Admitted: {res.admitted}"
            )
            for r in res.reasons:
                typer.echo(f"  - {r}")
            typer.echo(f"\n{LEGAL_DISCLAIMER}")
        if strict and not res.admitted:
            raise typer.Exit(code=1)
        return

    report = auditor.audit_all()

    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        typer.echo("=" * 70)
        typer.echo(f"XLM Dataset Source Admission Audit: {report['catalog_id']}")
        typer.echo("=" * 70)
        counts = report["counts"]
        typer.echo(f"Total Candidates:   {counts['total_candidates']}")
        typer.echo(f"Admitted:           {counts['admitted']}")
        typer.echo(f"Pending Review:     {counts['pending_review']}")
        typer.echo(f"Unadmitted:         {counts['unadmitted']}")
        typer.echo(f"Blocked:            {counts['blocked']}")
        typer.echo("-" * 70)
        for s in report["sources"]:
            status_tag = f"[{s['status'].upper()}]"
            typer.echo(f"{s['candidate_number']:<3} {s['source_id']:<22} {status_tag:<18}")
        typer.echo("-" * 70)
        typer.echo(f"\n{LEGAL_DISCLAIMER}\n")

    if strict and (counts["admitted"] != counts["total_candidates"] or counts["blocked"] > 0):
        raise typer.Exit(code=1)


@app.command("admit")
def admit_source_view(
    source_id: Annotated[str, typer.Option("--source", "-s", help="Candidate source ID.")],
    adapter_id: Annotated[str, typer.Option("--adapter", "-a", help="Tested adapter identifier.")],
    notes: Annotated[str, typer.Option("--notes", "-n", help="Operator review notes.")],
    view_id: Annotated[
        str, typer.Option("--view", "-v", help="View / subset identifier.")
    ] = "default",
    decision: Annotated[
        str, typer.Option("--decision", "-d", help="Decision: 'approve' or 'reject'.")
    ] = "approve",
    license_review: Annotated[
        str,
        typer.Option("--license-review", help="License review status: approved/pending/rejected."),
    ] = "pending",
    benchmark_risk: Annotated[
        str,
        typer.Option(
            "--benchmark-risk",
            help="Benchmark contamination risk: clean/suspect/disabled_pending_audit.",
        ),
    ] = "clean",
    catalog_path: Annotated[
        Path,
        typer.Option("--catalog", "-c", help="Path to dataset catalog YAML."),
    ] = Path("manifests/datasets.catalog.yaml"),
) -> None:
    """Explicit operator admission workflow persisting decisions into P01 artifact store."""
    try:
        catalog = load_catalog(catalog_path)
    except Exception as e:
        typer.echo(f"Error loading catalog: {e}", err=True)
        raise typer.Exit(code=1) from e

    cand = catalog.get_source(source_id)
    if not cand:
        typer.echo(f"Error: Source '{source_id}' not found in catalog.", err=True)
        raise typer.Exit(code=1)

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    evidence = load_probe_evidence(source_id, view_id, store)
    if not evidence:
        typer.echo(
            f"Error: No probe evidence found for '{source_id}:{view_id}'. "
            "You must probe the source before recording an admission decision.",
            err=True,
        )
        raise typer.Exit(code=1)

    is_approved = decision.strip().lower() == "approve"
    adm_decision = AdmissionDecision(
        source_id=source_id,
        view_id=view_id,
        provider=cand.provider,
        repository=cand.repository,
        immutable_revision=evidence.immutable_revision or "",
        adapter_id=adapter_id,
        probe_fingerprint=evidence.probe_fingerprint or "",
        license_review=license_review,
        benchmark_risk=benchmark_risk,
        operator_approved=is_approved,
        operator_notes=notes,
    )

    # Evaluate against admission gate
    gate_result = AdmissionGate.evaluate(evidence, adm_decision)
    art_dir = save_admission_decision(adm_decision, store)

    typer.echo(f"Recorded admission decision to artifact: {art_dir}")
    typer.echo(
        f"Gate evaluation: status={gate_result.status.value}, admitted={gate_result.admitted}"
    )
    for r in gate_result.reasons:
        typer.echo(f"  - {r}")


@app.command("plan")
def plan_cmd(
    source_id: Annotated[
        str,
        typer.Option("--source", "-s", help="Unique identifier of data source."),
    ],
    files: Annotated[
        str,
        typer.Option("--files", "-f", help="Comma-separated relative file paths."),
    ],
    view_id: Annotated[
        str,
        typer.Option("--view", "-v", help="View identifier within source."),
    ] = "default",
    catalog_path: Annotated[
        Path,
        typer.Option("--catalog", "-c", help="Path to dataset catalog YAML."),
    ] = Path("manifests/datasets.catalog.yaml"),
    output_path: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Path to write acquisition plan JSON."),
    ] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Acquisition mode: 'whole_file' or 'selected_records'."),
    ] = "whole_file",
    max_bytes: Annotated[
        int,
        typer.Option("--max-bytes", help="Maximum transferred byte limit."),
    ] = 256 * 1024 * 1024,
    max_records: Annotated[
        int,
        typer.Option("--max-records", help="Maximum records limit."),
    ] = 25_000,
    max_output_disk: Annotated[
        int,
        typer.Option("--max-output-disk", help="Maximum output disk limit."),
    ] = 2 * 1024 * 1024 * 1024,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Sampling seed."),
    ] = None,
    coverage: Annotated[
        str,
        typer.Option("--coverage", help="Coverage notes regarding date, domain, or shard."),
    ] = "",
    pilot_approved: Annotated[
        bool,
        typer.Option("--pilot-approved", help="Explicit operator pilot approval for local run."),
    ] = False,
    attempt: Annotated[
        int,
        typer.Option(
            "--attempt",
            help="Fresh-attempt counter (default 1). After an expired deadline, "
            "renew with a higher attempt: identical source/selection/limits, "
            "new plan identity and journal; the old attempt is preserved.",
        ),
    ] = 1,
    authorization_hash: Annotated[
        str | None,
        typer.Option("--authorization-hash", help="Authorization hash for production plan."),
    ] = None,
    row_ranges_path: Annotated[
        Path | None,
        typer.Option(
            "--row-ranges",
            help="JSON mapping original filenames to zero-based [start, stop) record ranges.",
        ),
    ] = None,
    limits_path: Annotated[
        Path | None,
        typer.Option(
            "--limits", help="JSON AcquisitionLimits; replaces the three convenience limit flags."
        ),
    ] = None,
    expected_digests_path: Annotated[
        Path | None,
        typer.Option(
            "--expected-digests",
            help="JSON original filename to independently reviewed SHA-256 mapping.",
        ),
    ] = None,
    project_fields: Annotated[
        str | None,
        typer.Option(
            "--project-fields",
            help="Comma-separated Parquet columns for selected_records (identity-bound).",
        ),
    ] = None,
    adapter_spec: Annotated[
        str | None,
        typer.Option(
            "--adapter-spec",
            help="Adapter spec 'adapter_id[:config]' resolving certified projection columns.",
        ),
    ] = None,
    coalesce_bytes: Annotated[
        int | None,
        typer.Option(
            "--coalesce-bytes",
            help="Gap threshold for coalescing adjacent Parquet column ranges.",
        ),
    ] = None,
    window_scan_rows: Annotated[
        int | None,
        typer.Option(
            "--parquet-window-scan-rows",
            help="Enable streamed sub-row-group window decode bounded to this many "
            "decoded rows per file (identity-bound; needs a projection).",
        ),
    ] = None,
    window_buffer_bytes: Annotated[
        int,
        typer.Option(
            "--parquet-window-buffer-bytes",
            help="Per-column stream buffer (= max range per column read) for window decode.",
        ),
    ] = 4 * 1024 * 1024,
    window_batch_rows: Annotated[
        int,
        typer.Option("--parquet-window-batch-rows", help="Decode batch rows for window decode."),
    ] = 256,
) -> None:
    """Generate and validate an acquisition plan adhering to Contracts C01 and C04."""
    try:
        catalog = load_catalog(catalog_path)
    except Exception as e:
        typer.echo(f"Error loading catalog: {e}", err=True)
        raise typer.Exit(code=1) from e

    cand = catalog.get_source(source_id)
    if not cand:
        typer.echo(f"Error: Source '{source_id}' not found in catalog.", err=True)
        raise typer.Exit(code=1)

    # Check P07 probe evidence for pinned revision
    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    evidence = load_probe_evidence(source_id, view_id, store)
    revision = (
        evidence.immutable_revision if evidence and evidence.immutable_revision else cand.revision
    )
    if not revision:
        typer.echo(
            f"Error: Source '{source_id}' lacks an immutable revision. "
            "Probe the source with 'xlm data probe' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    selected_files = [f.strip() for f in files.split(",") if f.strip()]
    if not selected_files:
        typer.echo("Error: At least one selected file must be specified.", err=True)
        raise typer.Exit(code=1)

    acq_mode = AcquisitionMode(mode)
    limits = AcquisitionLimits(
        max_transferred_bytes=max_bytes,
        max_records=max_records,
        max_output_disk_bytes=max_output_disk,
    )

    def bounded_json(path: Path | None) -> Any:
        if path is None:
            return None
        if path.stat().st_size > 1024**2:
            raise ValueError("plan input JSON exceeds 1 MiB")
        return json.loads(path.read_text(encoding="utf-8"))

    if limits_path is not None:
        if (max_bytes, max_records, max_output_disk) != (256 * 1024**2, 25000, 2 * 1024**3):
            raise ValueError("use either --limits or the convenience limit flags, not both")
        limits = AcquisitionLimits.model_validate(bounded_json(limits_path))
    if project_fields is not None and adapter_spec is not None:
        typer.echo("Error: use either --project-fields or --adapter-spec, not both.", err=True)
        raise typer.Exit(code=1)
    resolved_projection: list[str] | None = None
    if adapter_spec is not None:
        from xlm.data.adapters.columns import columns_for, parse_adapter_spec

        try:
            resolved_projection = list(columns_for(*parse_adapter_spec(adapter_spec)))
        except ValueError as e:
            typer.echo(f"Error: invalid --adapter-spec: {e}", err=True)
            raise typer.Exit(code=1) from e
    elif project_fields is not None:
        resolved_projection = [name.strip() for name in project_fields.split(",") if name.strip()]
        if not resolved_projection:
            typer.echo("Error: --project-fields must list at least one column.", err=True)
            raise typer.Exit(code=1)
    window: ParquetWindowDecode | None = None
    if window_scan_rows is not None:
        window = ParquetWindowDecode(
            stream_buffer_bytes=window_buffer_bytes,
            max_window_scan_rows=window_scan_rows,
            batch_rows=window_batch_rows,
        )
    elif (window_buffer_bytes, window_batch_rows) != (4 * 1024 * 1024, 256):
        typer.echo(
            "Error: --parquet-window-buffer-bytes/--parquet-window-batch-rows need "
            "--parquet-window-scan-rows.",
            err=True,
        )
        raise typer.Exit(code=1)
    sampling = SamplingFrame(
        selected_files=selected_files,
        selection_seed=seed,
        coverage_notes=coverage,
    )

    plan_id = f"plan_{source_id}_{view_id}_{cand.provider}"
    output_artifact_id = f"raw_{source_id}_{view_id}"

    # Build initial plan to compute its behavioral hash
    initial_plan = AcquisitionPlan(
        plan_id=plan_id,
        source_id=source_id,
        view_id=view_id,
        provider=cand.provider,
        repository=cand.repository,
        revision=revision,
        mode=acq_mode,
        selected_files=selected_files,
        sampling_frame=sampling,
        limits=limits,
        output_artifact_id=output_artifact_id,
        attempt=attempt,
        row_ranges=bounded_json(row_ranges_path),
        expected_file_digests=bounded_json(expected_digests_path) or {},
        projected_fields=resolved_projection,
        range_coalesce_bytes=coalesce_bytes,
        parquet_window=window,
    )
    # One gate decides pilot scope (legacy plans: the original three limits;
    # window plans: also the physical-work ceilings).
    initial_plan = initial_plan.model_copy(
        update={"is_pilot": not plan_requires_production_admission(initial_plan)}
    )
    identity_suffix = initial_plan.compute_behavioral_hash()[:20]
    initial_plan = initial_plan.model_copy(
        update={
            "plan_id": f"{plan_id}_{identity_suffix}",
            "output_artifact_id": f"{output_artifact_id}_{identity_suffix}",
        }
    )
    b_hash = initial_plan.compute_behavioral_hash()

    auth: PlanAuthorization | None = None
    if pilot_approved:
        from datetime import datetime

        auth = PlanAuthorization(
            authorization_hash=b_hash,
            authorized_by="local_operator_cli",
            authorized_at=datetime.now(UTC).isoformat(),
            scope="pilot",
            is_pilot_approved=True,
        )
    elif authorization_hash:
        from datetime import datetime

        auth = PlanAuthorization(
            authorization_hash=authorization_hash,
            authorized_by="operator_authorization",
            authorized_at=datetime.now(UTC).isoformat(),
            scope="production",
            is_pilot_approved=False,
        )

    resolved_plan = initial_plan.model_copy(update={"authorization": auth, "plan_hash": b_hash})

    out_file = output_path or Path(f"plans/{initial_plan.plan_id}.json")
    save_acquisition_plan(resolved_plan, out_file)

    typer.echo("============================================================")
    typer.echo(f"Acquisition Plan Generated: {resolved_plan.plan_id}")
    typer.echo(f"Source ID:     {resolved_plan.source_id}:{resolved_plan.view_id}")
    typer.echo(f"Repository:    {resolved_plan.repository} (revision: {resolved_plan.revision})")
    typer.echo(f"Mode:          {resolved_plan.mode.value}")
    typer.echo(f"Selected:      {len(resolved_plan.selected_files)} file(s)")
    if resolved_plan.projected_fields is not None:
        typer.echo(f"Projected:     {', '.join(sorted(resolved_plan.projected_fields))}")
    if resolved_plan.range_coalesce_bytes is not None:
        typer.echo(f"Coalesce:      <= {resolved_plan.range_coalesce_bytes:,} byte gaps")
    if resolved_plan.parquet_window is not None:
        pw = resolved_plan.parquet_window
        typer.echo(
            f"Window:        v{pw.policy_version} scan <= {pw.max_window_scan_rows:,} rows/file, "
            f"buffer {pw.stream_buffer_bytes:,} B, batch {pw.batch_rows}"
        )
    typer.echo(f"Transferred:   <= {resolved_plan.limits.max_transferred_bytes:,} bytes")
    typer.echo(f"Output Disk:   <= {resolved_plan.limits.max_output_disk_bytes:,} bytes")
    typer.echo(f"Behavior Hash: {resolved_plan.plan_hash}")
    is_appr = bool(auth and auth.is_pilot_approved)
    typer.echo(f"Pilot Scope:   {resolved_plan.is_pilot} (Approved: {is_appr})")
    typer.echo(f"Saved to:      {out_file}")
    typer.echo("============================================================")


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write deterministic JSON (sorted keys) via atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@app.command("sample-blocks")
def sample_blocks_cmd(
    source_id: Annotated[
        str,
        typer.Option("--source", "-s", help="Unique identifier of data source."),
    ],
    files: Annotated[
        str,
        typer.Option("--files", "-f", help="Comma-separated candidate Parquet file paths."),
    ],
    view_id: Annotated[
        str, typer.Option("--view", "-v", help="View identifier within source.")
    ] = "default",
    catalog_path: Annotated[
        Path,
        typer.Option("--catalog", "-c", help="Path to dataset catalog YAML."),
    ] = Path("manifests/datasets.catalog.yaml"),
    revision_override: Annotated[
        str | None,
        typer.Option("--revision", help="Pin an immutable revision explicitly."),
    ] = None,
    seed: Annotated[int, typer.Option("--seed", help="Deterministic sampling seed.")] = 0,
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Block mode: 'rowgroup', 'contiguous', or 'window'."),
    ] = "rowgroup",
    block_records: Annotated[
        int,
        typer.Option("--block-records", help="Minimum records per block (contiguous mode)."),
    ] = 1000,
    target_records: Annotated[
        int, typer.Option("--target-records", help="Goal retained-record count.")
    ] = 1000,
    target_tokens: Annotated[
        float | None,
        typer.Option("--target-tokens", help="Goal token count (needs --tokens-per-record)."),
    ] = None,
    tokens_per_record: Annotated[
        float | None,
        typer.Option("--tokens-per-record", help="Operator token factor for estimates."),
    ] = None,
    max_overshoot_records: Annotated[
        int,
        typer.Option("--max-overshoot-records", help="Allowed records beyond target."),
    ] = 0,
    max_blocks_per_file: Annotated[
        int | None,
        typer.Option("--max-blocks-per-file", help="Cap row groups spanned per file."),
    ] = None,
    max_records: Annotated[
        int | None,
        typer.Option("--max-records", help="Hard cap on planned records."),
    ] = None,
    max_bytes: Annotated[
        int | None,
        typer.Option("--max-bytes", help="Hard cap on estimated uncompressed bytes."),
    ] = None,
    max_parser_bytes: Annotated[
        int, typer.Option("--max-parser-bytes", help="Parser byte bound per row group.")
    ] = 32 * 1024 * 1024,
    max_decompression_ratio: Annotated[
        float, typer.Option("--max-decompression-ratio", help="Decompression ratio bound.")
    ] = 15.0,
    local_dir: Annotated[
        Path | None,
        typer.Option("--local-dir", help="Local directory holding the candidate files."),
    ] = None,
    metadata_bytes: Annotated[
        int, typer.Option("--metadata-bytes", help="Remote footer-discovery byte budget.")
    ] = 8 * 1024 * 1024,
    metadata_requests: Annotated[
        int, typer.Option("--metadata-requests", help="Remote footer-discovery request budget.")
    ] = 100,
    output_path: Annotated[
        Path,
        typer.Option("--output", "-o", help="Path to write row-ranges JSON for data plan."),
    ] = Path("sample-blocks.json"),
    report_path: Annotated[
        Path | None,
        typer.Option("--report", help="Path to write sampling evidence JSON."),
    ] = None,
    adapter_spec: Annotated[
        str | None,
        typer.Option(
            "--adapter-spec",
            help="Window mode: adapter spec 'adapter_id[:config]' for the certified projection.",
        ),
    ] = None,
    project_fields: Annotated[
        str | None,
        typer.Option("--project-fields", help="Window mode: comma-separated projected columns."),
    ] = None,
    window_scan_rows: Annotated[
        int,
        typer.Option(
            "--window-max-scan-rows", help="Window mode: max decoded rows per file window."
        ),
    ] = 16384,
    window_buffer_bytes: Annotated[
        int,
        typer.Option("--window-buffer-bytes", help="Window mode: per-column stream buffer."),
    ] = 4 * 1024 * 1024,
    window_batch_rows: Annotated[
        int,
        typer.Option("--window-batch-rows", help="Window mode: decode batch rows."),
    ] = 256,
) -> None:
    """Plan dense row-group-aligned selections without acquiring records.

    Discovers Parquet footers (local files or bounded remote range reads),
    then derives deterministic ``row_ranges`` usable directly by
    ``xlm data plan --row-ranges``. Never fetches record payloads.
    """
    selected_files = [entry.strip() for entry in files.split(",") if entry.strip()]
    if not selected_files:
        typer.echo("Error: At least one candidate file must be specified.", err=True)
        raise typer.Exit(code=1)
    if mode not in ("rowgroup", "contiguous", "window"):
        typer.echo("Error: --mode must be 'rowgroup', 'contiguous', or 'window'.", err=True)
        raise typer.Exit(code=1)
    projection: tuple[str, ...] | None = None
    window: ParquetWindowDecode | None = None
    if mode == "window":
        if (adapter_spec is None) == (project_fields is None):
            typer.echo(
                "Error: --mode window needs exactly one of --adapter-spec / --project-fields.",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            if adapter_spec is not None:
                from xlm.data.adapters.columns import columns_for, parse_adapter_spec

                projection = tuple(columns_for(*parse_adapter_spec(adapter_spec)))
            else:
                projection = tuple(
                    name.strip() for name in (project_fields or "").split(",") if name.strip()
                )
            window = ParquetWindowDecode(
                stream_buffer_bytes=window_buffer_bytes,
                max_window_scan_rows=window_scan_rows,
                batch_rows=window_batch_rows,
            )
        except ValueError as e:
            typer.echo(f"Error: invalid window sampling options: {e}", err=True)
            raise typer.Exit(code=1) from e
    elif adapter_spec is not None or project_fields is not None:
        typer.echo("Error: projection options apply to --mode window only.", err=True)
        raise typer.Exit(code=1)
    if target_tokens is not None and tokens_per_record is None:
        typer.echo("Error: --target-tokens needs an explicit --tokens-per-record factor.", err=True)
        raise typer.Exit(code=1)
    try:
        catalog = load_catalog(catalog_path)
    except Exception as e:
        typer.echo(f"Error loading catalog: {e}", err=True)
        raise typer.Exit(code=1) from e
    cand = catalog.get_source(source_id)
    if not cand:
        typer.echo(f"Error: Source '{source_id}' not found in catalog.", err=True)
        raise typer.Exit(code=1)
    resolved_revision = revision_override
    if resolved_revision is None:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        evidence = load_probe_evidence(source_id, view_id, store)
        resolved_revision = (
            evidence.immutable_revision
            if evidence and evidence.immutable_revision
            else cand.revision
        )
    if not resolved_revision:
        typer.echo(
            f"Error: Source '{source_id}' lacks an immutable revision. "
            "Probe the source with 'xlm data probe' first or pass --revision.",
            err=True,
        )
        raise typer.Exit(code=1)
    revision: str = resolved_revision
    try:
        if local_dir is not None:
            layouts: dict[str, FileLayout] = {}
            for name in selected_files:
                candidate = local_dir / name
                if not candidate.is_file():
                    raise SamplingRefusal(f"local candidate file not found: '{name}'")
                layouts[name] = discover_layout_local(
                    candidate,
                    name=name,
                    max_parser_bytes=max_parser_bytes,
                    max_decompression_ratio=max_decompression_ratio,
                )
            discovery = "local"
        else:
            layouts = _discover_remote_layouts(
                provider=cand.provider,
                repository=cand.repository,
                revision=revision,
                files=selected_files,
                max_parser_bytes=max_parser_bytes,
                max_decompression_ratio=max_decompression_ratio,
                metadata_bytes=metadata_bytes,
                metadata_requests=metadata_requests,
            )
            discovery = "remote"
        result = plan_sample_blocks(
            layouts,
            SamplingRequest(
                source_id=source_id,
                view_id=view_id,
                revision=revision,
                files=tuple(selected_files),
                seed=seed,
                mode=mode,
                block_records=block_records,
                target_records=target_records,
                max_overshoot_records=max_overshoot_records,
                max_blocks_per_file=max_blocks_per_file,
                max_records=max_records,
                max_uncompressed_bytes=max_bytes,
                max_parser_bytes=max_parser_bytes,
                max_decompression_ratio=max_decompression_ratio,
                tokens_per_record=tokens_per_record,
                projected_fields=projection,
                window=window,
            ),
        )
    except SamplingRefusal as exc:
        typer.echo(f"Sampling refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (BudgetExhaustedError, HostNotAllowlistedError, TimeoutError) as exc:
        typer.echo(f"Sampling refused: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    ranges_payload = {
        name: [start, stop] for name, (start, stop) in sorted(result.row_ranges.items())
    }
    report = result.to_report()
    report["discovery"] = discovery
    report["metadata_budgets"] = {
        "max_bytes": metadata_bytes,
        "max_requests": metadata_requests,
    }
    if target_tokens is not None:
        report["target_tokens"] = target_tokens
    resolved_report = report_path or output_path.parent / (output_path.stem + ".evidence.json")
    _atomic_write_json(output_path, ranges_payload)
    _atomic_write_json(resolved_report, report)
    typer.echo("============================================================")
    typer.echo(f"Sampling Plan: {source_id}:{view_id} rev {revision}")
    typer.echo(f"Seed: {seed}  Mode: {mode}  Discovery: {discovery}")
    typer.echo(f"Candidates: {len(selected_files)} file(s)  Selected: {len(result.selected_files)}")
    for block in result.blocks:
        typer.echo(
            f"  - {block.file}: rows [{block.start_row},{block.stop_row}) "
            f"({block.num_rows} rows, groups [{block.group_start},{block.group_stop_exclusive}))"
        )
    for chosen in result.windows:
        typer.echo(
            f"  - {chosen.file}: rows [{chosen.start_row},{chosen.stop_row}) "
            f"({chosen.num_rows} rows) in row group {chosen.row_group} of "
            f"{chosen.group_rows} rows; decodes {chosen.expected_scan_rows} rows; "
            f"projected chunks {chosen.selected_compressed_bytes:,} B compressed / "
            f"{chosen.selected_uncompressed_bytes:,} B uncompressed "
            f"(group {chosen.group_total_byte_size:,} B); est. transfer <= "
            f"{chosen.estimated_transfer_upper_bytes:,} B in ~{chosen.estimated_requests} requests"
        )
    typer.echo(
        f"Requested: {result.requested_records}  Planned: {result.planned_records}  "
        f"Overshoot: {result.overshoot_records}"
    )
    typer.echo(
        f"Estimated uncompressed: {result.estimated_uncompressed_bytes:,} bytes  "
        f"compressed: {result.estimated_compressed_bytes:,} bytes  "
        f"transfer: unknown"
    )
    if result.estimated_tokens is not None:
        typer.echo(f"Estimated tokens: {result.estimated_tokens:,.1f}")
    for warning in result.warnings:
        typer.echo(f"Warning: {warning}")
    typer.echo(f"Row ranges: {output_path}")
    typer.echo(f"Evidence:   {resolved_report}")
    window_next = ""
    if window is not None:
        window_next = (
            f" --parquet-window-scan-rows {window.max_window_scan_rows}"
            f" --parquet-window-buffer-bytes {window.stream_buffer_bytes}"
            f" --parquet-window-batch-rows {window.batch_rows}"
        )
    typer.echo(
        f"Next: xlm data plan --source {source_id} --view {view_id} --catalog {catalog_path} "
        f"--files {','.join(result.selected_files)} --mode selected_records "
        f"--row-ranges {output_path} --seed {seed}{window_next} [...]"
    )
    typer.echo("============================================================")


def _discover_remote_layouts(
    *,
    provider: str,
    repository: str,
    revision: str,
    files: list[str],
    max_parser_bytes: int,
    max_decompression_ratio: float,
    metadata_bytes: int,
    metadata_requests: int,
) -> dict[str, FileLayout]:
    """Bounded remote footer discovery: range reads only, never record payloads."""
    from xlm.data.sources.transport import BudgetExhaustedError

    budget = TransportBudget(max_bytes=metadata_bytes, max_requests=metadata_requests)
    opener = urllib.request.build_opener(SafeRedirectHandler(budget))

    def fetch_for(rel_path: str) -> Any:
        url = canonical_range_url(provider, repository, revision, rel_path)
        validate_host(url)

        def fetch(start: int, end: int) -> tuple[bytes, int]:
            if not 0 <= start <= end or end - start + 1 > max_parser_bytes:
                raise SamplingRefusal(f"metadata range for '{rel_path}' exceeds parser bound")
            budget.record_request()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "xlm-acquisition/2",
                    "Accept-Encoding": "identity",
                    "Range": f"bytes={start}-{end}",
                },
            )
            try:
                with opener.open(request, timeout=15.0) as response:
                    match = CONTENT_RANGE_RE.fullmatch(response.headers.get("Content-Range", ""))
                    if (
                        response.status != 206
                        or not match
                        or (int(match[1]), int(match[2])) != (start, end)
                    ):
                        raise SamplingRefusal(f"metadata range for '{rel_path}' refused by server")
                    body = budget.read_body(response, end - start + 1)
                    return body, int(match[3])
            except SamplingRefusal:
                raise
            except BudgetExhaustedError as exc:
                raise SamplingRefusal(
                    f"metadata budget exhausted while discovering '{rel_path}': {exc}"
                ) from exc
            except Exception as exc:
                raise SamplingRefusal(
                    f"cannot discover layout for '{rel_path}': {type(exc).__name__}"
                ) from exc

        return fetch

    layouts: dict[str, FileLayout] = {}
    for name in files:
        layouts[name] = discover_layout_over_ranges(
            name,
            fetch_for(name),
            max_parser_bytes=max_parser_bytes,
            max_decompression_ratio=max_decompression_ratio,
        )
    return layouts


@app.command("fetch")
def fetch_cmd(
    plan_path: Annotated[
        Path,
        typer.Option("--plan", "-p", help="Path to acquisition plan JSON file."),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Directory for acquired raw files."),
    ] = None,
    scratch_dir: Annotated[
        Path | None,
        typer.Option("--scratch-dir", help="Directory for scratch files and journal."),
    ] = None,
    pilot_approved: Annotated[
        bool,
        typer.Option(
            "--pilot-approved", help="Explicit operator confirmation for pilot execution."
        ),
    ] = False,
) -> None:
    """Execute bounded data acquisition adhering to plan limits and progress journal."""
    try:
        plan = load_acquisition_plan(plan_path)
    except Exception as e:
        typer.echo(f"Error loading plan: {e}", err=True)
        raise typer.Exit(code=1) from e

    if (
        pilot_approved
        and plan.is_pilot
        and (not plan.authorization or not plan.authorization.is_pilot_approved)
    ):
        from datetime import datetime

        auth = PlanAuthorization(
            authorization_hash=plan.compute_behavioral_hash(),
            authorized_by="local_operator_cli_fetch",
            authorized_at=datetime.now(UTC).isoformat(),
            scope="pilot",
            is_pilot_approved=True,
        )
        plan = plan.model_copy(update={"authorization": auth})

    paths = ArtifactPaths.from_env()
    target_output = output_dir or paths.root / "acquisition" / plan.plan_id / "raw"
    target_scratch = scratch_dir or paths.root / "acquisition" / plan.plan_id / "scratch"

    from xlm.artifacts.store import ArtifactStore
    from xlm.data.acquisition.plan import plan_requires_production_admission
    from xlm.data.sources.admission import resolve_verified_production_admission

    catalog_approved = False
    if plan_requires_production_admission(plan):
        # Production scope: resolve the stored admission decision bound to this
        # exact source/view/revision. Pilot plans never enter this branch.
        resolve_verified_production_admission(plan, ArtifactStore(paths))
        catalog_approved = True

    try:
        fetcher = BoundedFetcher(
            plan,
            scratch_dir=target_scratch,
            output_dir=target_output,
            catalog_source_approved=catalog_approved,
        )
        typer.echo(f"Starting acquisition for plan '{plan.plan_id}'...")
        state = fetcher.run()
        typer.echo(f"Acquisition finished with status: {state.status}")
        typer.echo(
            f"Transferred: {state.transferred_bytes:,} bytes ({state.requests_made} requests)"
        )
        typer.echo(f"Cache Hits:  {state.cache_hits}")
        typer.echo(f"Corpus records: {state.records_acquired} (opaque files are not counted)")
        typer.echo(f"Output dir:  {target_output}")
    except Exception as e:
        typer.echo(f"Acquisition failed: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("status")
def status_cmd(
    plan_path: Annotated[
        Path,
        typer.Option("--plan", "-p", help="Path to acquisition plan JSON file."),
    ],
    scratch_dir: Annotated[
        Path | None,
        typer.Option("--scratch-dir", help="Directory for scratch files and journal."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output status as structured JSON."),
    ] = False,
) -> None:
    """Inspect progress journal and cumulative resource usage for an acquisition plan."""
    try:
        plan = load_acquisition_plan(plan_path)
    except Exception as e:
        typer.echo(f"Error loading plan: {e}", err=True)
        raise typer.Exit(code=1) from e

    target_scratch = (
        scratch_dir or ArtifactPaths.from_env().root / "acquisition" / plan.plan_id / "scratch"
    )
    journal_path = target_scratch / "journals" / f"{plan.plan_id}.progress.json"

    if not journal_path.exists():
        typer.echo(f"No progress journal found at '{journal_path}'. Acquisition not started.")
        return

    journal = ProgressJournal(journal_path, plan.plan_id, plan.compute_behavioral_hash())
    if as_json:
        typer.echo(json.dumps(journal.state.model_dump(), indent=2))
    else:
        st = journal.state
        typer.echo("============================================================")
        typer.echo(f"Acquisition Status: {st.plan_id} ({st.status})")
        typer.echo(f"Transferred Bytes:  {st.transferred_bytes:,} bytes")
        typer.echo(f"Requests Made:      {st.requests_made}")
        typer.echo(f"Cache Hits:         {st.cache_hits}")
        typer.echo(f"Temp Disk Bytes:    {st.temp_disk_bytes:,} bytes")
        typer.echo(f"Output Disk Bytes:  {st.output_disk_bytes:,} bytes")
        typer.echo(f"Files Tracked:      {len(st.file_progress)}")
        for fpath, fp in st.file_progress.items():
            typer.echo(f"  - {fpath}: {fp.status} ({fp.bytes_downloaded:,} bytes)")
        if st.error_reason:
            typer.echo(f"Error Reason:       {st.error_reason}")
        typer.echo("============================================================")


@app.command("performance")
def performance_cmd(
    plan_path: Annotated[
        Path,
        typer.Option("--plan", "-p", help="Path to acquisition plan JSON file."),
    ],
    scratch_dir: Annotated[
        Path | None,
        typer.Option("--scratch-dir", help="Directory for scratch files and telemetry."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output performance telemetry as structured JSON."),
    ] = False,
) -> None:
    """Read existing acquisition-performance telemetry without redoing the fetch.

    Answers "Where did the time go?" and "Was worker parallelism used?"
    from the versioned sidecar written by the fetch. Never launches network
    requests and never modifies journals, plans, or artifacts.
    """
    try:
        plan = load_acquisition_plan(plan_path)
    except Exception as e:
        typer.echo(f"Error loading plan: {e}", err=True)
        raise typer.Exit(code=1) from e

    target_scratch = (
        scratch_dir or ArtifactPaths.from_env().root / "acquisition" / plan.plan_id / "scratch"
    )
    sidecar = target_scratch / "performance" / f"{plan.plan_id}.perf.json"
    if not sidecar.is_file():
        journal_path = target_scratch / "journals" / f"{plan.plan_id}.progress.json"
        if journal_path.is_file():
            try:
                journal = ProgressJournal(
                    journal_path, plan.plan_id, plan.compute_behavioral_hash()
                )
            except Exception as e:
                typer.echo(f"Error loading journal: {e}", err=True)
                raise typer.Exit(code=1) from e
            st = journal.state
            if as_json:
                typer.echo(
                    json.dumps(
                        {
                            "plan_id": st.plan_id,
                            "status": st.status,
                            "telemetry_available": False,
                            "note": "no performance sidecar; pre-telemetry journal still loads",
                            "transferred_bytes": st.transferred_bytes,
                            "requests_made": st.requests_made,
                            "cache_hits": st.cache_hits,
                            "records_acquired": st.records_acquired,
                        },
                        indent=2,
                    )
                )
            else:
                typer.echo("============================================================")
                typer.echo(f"Acquisition Performance: {st.plan_id} ({st.status})")
                typer.echo("Telemetry: not available (pre-telemetry journal still loads)")
                typer.echo(f"Transferred: {st.transferred_bytes:,} bytes")
                typer.echo(f"Requests:    {st.requests_made}")
                typer.echo(f"Cache Hits:  {st.cache_hits}")
                typer.echo("============================================================")
            return
        typer.echo(f"No performance telemetry found at '{sidecar}'. Fetch not yet run.", err=True)
        raise typer.Exit(code=1)

    from xlm.data.acquisition.perf import load_perf_doc

    try:
        doc = load_perf_doc(sidecar)
    except Exception as e:
        typer.echo(f"Error loading performance telemetry: {e}", err=True)
        raise typer.Exit(code=1) from e

    if as_json:
        typer.echo(json.dumps(doc, indent=2))
        return
    _print_perf_summary(doc)


def _print_perf_summary(doc: dict[str, Any]) -> None:
    telemetry = doc.get("telemetry", {})
    rates = doc.get("rates", {})
    shares = doc.get("time_shares_of_wall", {})
    wall = float(doc.get("wall_seconds", 0.0) or 0.0)
    transferred = int(doc.get("transferred_bytes", 0) or 0)
    decompressed = int(doc.get("decompressed_bytes", 0) or 0)
    typer.echo("============================================================")
    typer.echo(f"Acquisition Performance: {doc.get('plan_id')} ({doc.get('status')})")
    typer.echo(f"Source: {doc.get('source_id')}:{doc.get('view_id')} rev {doc.get('revision')}")
    typer.echo(f"Mode: {doc.get('mode')} attempt {doc.get('attempt')}")
    typer.echo(f"Wall: {wall:.3f}s  CPU: {doc.get('cpu_process_seconds')}")
    typer.echo(
        f"Transferred: {transferred:,} bytes "
        f"({transferred / (1024**2):.3f} MiB @ "
        f"{float(rates.get('application_mb_per_sec', 0.0) or 0.0):.3f} MiB/s)"
    )
    typer.echo(
        f"Decompressed: {decompressed:,} bytes "
        f"(@ {float(rates.get('decompressed_mb_per_sec', 0.0) or 0.0):.3f} MiB/s)"
    )
    typer.echo(
        f"Scanned: {telemetry.get('scanned_records')} "
        f"(@ {float(rates.get('records_scanned_per_sec', 0.0) or 0.0):.2f}/s)  "
        f"Retained: {telemetry.get('retained_records')} "
        f"(@ {float(rates.get('retained_records_per_sec', 0.0) or 0.0):.2f}/s)"
    )
    typer.echo(
        f"Logical requests: {telemetry.get('logical_requests', telemetry.get('requests'))} "
        f"(@ {float(rates.get('requests_per_sec', 0.0) or 0.0):.2f}/s)  "
        f"Redirects: {telemetry.get('redirect_requests', telemetry.get('redirects'))}  "
        f"Retries: {telemetry.get('retries')}"
    )
    typer.echo(
        f"Accounted network requests: "
        f"{telemetry.get('accounted_network_requests', telemetry.get('requests'))} "
        f"(journal requests_made {doc.get('requests_made')})"
    )
    typer.echo(
        f"Redirect-target cache: hits {telemetry.get('redirect_target_cache_hits', 0)} "
        f"misses {telemetry.get('redirect_target_cache_misses', 0)} "
        f"invalidations {telemetry.get('redirect_target_invalidations', 0)}  "
        f"Connections (pooled direct only): reuses "
        f"{telemetry.get('connection_reuses', 0)} creations "
        f"{telemetry.get('connection_creations', 0)}"
    )
    if doc.get("projected_fields") is not None:
        skipped = int(telemetry.get("projection_skipped_bytes", 0) or 0)
        selected = int(telemetry.get("projection_selected_bytes", 0) or 0)
        total = selected + skipped
        ratio = (skipped / total) if total > 0 else 0.0
        typer.echo(
            f"Projection: {', '.join(doc.get('projected_fields') or [])}  "
            f"skipped {skipped:,} bytes ({ratio * 100:.1f}%)  "
            f"coalesced {telemetry.get('coalesced_ranges', 0)} ranges "
            f"(gap {telemetry.get('coalesced_gap_bytes', 0):,} bytes)  "
            f"column chunks {telemetry.get('column_chunks_read', 0)}"
        )
    if telemetry.get("peak_rss_bytes"):
        typer.echo(f"Peak worker RSS: {int(telemetry['peak_rss_bytes']):,} bytes")
    journal = doc.get("journal") or {}
    if journal:
        typer.echo(
            f"Journal: {journal.get('journal_transactions', 0)} transactions, "
            f"{journal.get('journal_persisted_writes', 0)} persisted writes, "
            f"{journal.get('journal_fsyncs', 0)} fsyncs, "
            f"{journal.get('journal_bytes_written', 0):,} bytes written"
        )
    typer.echo("Where did the time go (share of wall)?")
    for key in (
        "request_open",
        "body_stream",
        "parquet_metadata",
        "parquet_decode",
        "serialize_write",
        "accounting_lock",
        "retry_wait",
    ):
        typer.echo(f"  - {key}: {float(shares.get(key, 0.0) or 0.0) * 100:.1f}%")
    typer.echo(f"Slowest stage: {doc.get('slowest_stage')}")
    typer.echo(
        f"Workers: configured {doc.get('max_workers_configured')} "
        f"max observed {telemetry.get('max_active_workers')} "
        f"avg concurrency {float(doc.get('average_concurrency', 0.0) or 0.0):.2f}"
    )
    typer.echo(
        f"Cache: {doc.get('cache_class')} "
        f"(hits {doc.get('cache_hits')}, telemetry hits "
        f"{telemetry.get('cache_hits_telemetry')})"
    )
    slowest = doc.get("slowest_requests", []) or []
    if slowest:
        typer.echo(f"Slowest requests (top {len(slowest)}):")
        for item in slowest[:8]:
            typer.echo(
                f"  - {item.get('category')}/{item.get('file')} "
                f"via {item.get('host')}: {float(item.get('seconds', 0.0) or 0.0):.3f}s"
            )
    slowest_files = doc.get("slowest_files", []) or []
    if slowest_files:
        typer.echo(f"Slowest files (top {len(slowest_files)}):")
        for item in slowest_files[:8]:
            typer.echo(f"  - {item.get('file')}: {float(item.get('seconds', 0.0) or 0.0):.3f}s")
    typer.echo("Notes: application response-body bytes only; no TCP/TLS wire accounting.")
    typer.echo(
        "Notes: parquet_decode is inclusive row-group processing (range I/O + CPU "
        "decode), not CPU-only; accounted requests = logical + redirects."
    )
    typer.echo("============================================================")


@app.command("performance-compare")
def performance_compare_cmd(
    perf_files: Annotated[
        list[Path] | None,
        typer.Option("--perf", help="Performance sidecar JSON files to compare."),
    ] = None,
    plan_files: Annotated[
        list[Path] | None,
        typer.Option("--plan", help="Acquisition plan files; sidecars resolve via scratch."),
    ] = None,
    scratch_dir: Annotated[
        Path | None,
        typer.Option(
            "--scratch-dir",
            help="Shared scratch root when plans used custom layouts; otherwise per-plan defaults.",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output comparison as structured JSON."),
    ] = False,
) -> None:
    """Compare offline performance sidecars across worker configurations.

    Only compares logically equivalent workloads (same source/view/revision/
    files/ranges/mode/limits, same cache class, all COMPLETED). Attempt and
    max_workers may differ — that is the comparison. Never launches fetches.
    """
    from xlm.data.acquisition.perf import compare_perf_docs, load_perf_doc

    docs: list[dict[str, Any]] = []
    for sidecar in perf_files or []:
        try:
            docs.append(load_perf_doc(sidecar))
        except Exception as e:
            typer.echo(f"Error loading performance telemetry: {e}", err=True)
            raise typer.Exit(code=1) from e
    for plan_path in plan_files or []:
        try:
            plan = load_acquisition_plan(plan_path)
        except Exception as e:
            typer.echo(f"Error loading plan: {e}", err=True)
            raise typer.Exit(code=1) from e
        target_scratch = (
            scratch_dir or ArtifactPaths.from_env().root / "acquisition" / plan.plan_id / "scratch"
        )
        sidecar = target_scratch / "performance" / f"{plan.plan_id}.perf.json"
        try:
            docs.append(load_perf_doc(sidecar))
        except Exception as e:
            typer.echo(f"Error loading performance telemetry: {e}", err=True)
            raise typer.Exit(code=1) from e
    if len(docs) < 2:
        typer.echo(
            "Comparison needs at least two performance documents "
            "(--perf sidecars or --plan files).",
            err=True,
        )
        raise typer.Exit(code=1)
    result = compare_perf_docs(docs)
    if as_json:
        typer.echo(json.dumps(result, indent=2))
        if not result.get("comparable"):
            raise typer.Exit(code=1)
        return
    if not result.get("comparable"):
        typer.echo("Performance comparison refused: workloads are not equivalent.")
        for reason in result.get("refusals", []):
            typer.echo(f"  - {reason}")
        raise typer.Exit(code=1)
    typer.echo("============================================================")
    typer.echo("Acquisition Performance Comparison (equivalent workloads only)")
    for entry in result.get("entries", []):
        typer.echo(
            f"  - {entry.get('plan_id')} attempt {entry.get('attempt')} "
            f"workers {entry.get('max_workers')}: "
            f"{float(entry.get('wall_seconds', 0.0) or 0.0):.3f}s, "
            f"{float(entry.get('application_mb_per_sec', 0.0) or 0.0):.3f} MiB/s app, "
            f"{float(entry.get('decompressed_mb_per_sec', 0.0) or 0.0):.3f} MiB/s decomp, "
            f"max concurrency {entry.get('max_active_workers')} "
            f"(avg {float(entry.get('average_concurrency', 0.0) or 0.0):.2f}), "
            f"slowest {entry.get('slowest_stage')}, cache {entry.get('cache_class')}"
        )
    fastest = result.get("fastest", {})
    typer.echo(
        f"Observed fastest (this workload only, not a universal recommendation): "
        f"{fastest.get('plan_id')} workers {fastest.get('max_workers')} "
        f"({float(fastest.get('wall_seconds', 0.0) or 0.0):.3f}s)"
    )
    typer.echo("============================================================")


@app.command("verify")
def verify_cmd(
    plan_path: Annotated[
        Path,
        typer.Option("--plan", "-p", help="Path to acquisition plan JSON file."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory containing acquired raw files."),
    ],
    publish: Annotated[
        bool,
        typer.Option(
            "--publish/--no-publish", help="Publish verified artifact to P01 ArtifactStore."
        ),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output verification receipt as structured JSON."),
    ] = False,
    scratch_dir: Annotated[
        Path | None,
        typer.Option("--scratch-dir", help="Original acquisition scratch/journal directory."),
    ] = None,
) -> None:
    """Verify integrity of acquired files and publish immutable raw_dataset artifact."""
    try:
        plan = load_acquisition_plan(plan_path)
    except Exception as e:
        typer.echo(f"Error loading plan: {e}", err=True)
        raise typer.Exit(code=1) from e

    target_scratch = (
        scratch_dir or ArtifactPaths.from_env().root / "acquisition" / plan.plan_id / "scratch"
    )
    journal_path = target_scratch / "journals" / f"{plan.plan_id}.progress.json"
    if not journal_path.is_file():
        typer.echo(
            "Verification requires the original journal; missing provenance remains unresolved.",
            err=True,
        )
        raise typer.Exit(code=1)
    journal = ProgressJournal(journal_path, plan.plan_id, plan.compute_behavioral_hash())
    verifier = AcquisitionVerifier(plan, output_dir=output_dir, journal=journal)
    try:
        receipt = verifier.verify()
    except Exception as e:
        typer.echo(f"Verification error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if publish:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        art_dir = verifier.publish_artifact(store, receipt)
        typer.echo(f"Successfully published raw_dataset artifact to: {art_dir}", err=as_json)

    if as_json:
        typer.echo(json.dumps(receipt.model_dump(), indent=2))
    else:
        typer.echo("============================================================")
        typer.echo(f"Acquisition Verification Receipt: {receipt.receipt_id}")
        typer.echo(f"Plan ID:     {receipt.plan_id}")
        typer.echo(f"Eligibility: {receipt.eligibility}")
        typer.echo(f"Files ({len(receipt.files)}):")
        for f in receipt.files:
            typer.echo(
                f"  - {f.relative_path}: {f.size_bytes:,} bytes, "
                f"sha256={f.locally_computed_sha256[:16]}..., "
                f"rows={f.record_count}"
            )
        for n in receipt.verification_notes:
            typer.echo(f"  Note: {n}")
        typer.echo("============================================================")


@app.command("adapt")
def adapt_cmd(
    plan_path: Annotated[
        Path,
        typer.Option("--plan", "-p", help="Reviewed acquisition plan JSON file."),
    ],
    adapter_id: Annotated[
        str,
        typer.Option("--adapter", "-a", help="Tested mix01 adapter identifier."),
    ],
    input_path: Annotated[
        Path,
        typer.Option("--input", "-i", help="Verified selected_records.jsonl artifact file."),
    ] = Path("selected_records.jsonl"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for CanonicalDocument documents.jsonl."),
    ] = Path("data/canonical/adapted"),
    on_reject: Annotated[
        str,
        typer.Option(
            "--on-reject",
            help="Policy-rejection handling: 'fail' aborts on the first reject; "
            "'record' records recognized RecordRejectedError policy rejects and continues.",
        ),
    ] = "fail",
    adapter_config: Annotated[
        str | None,
        typer.Option(
            "--adapter-config",
            help="Explicit constructor config for parameterized adapters "
            "(e.g. a Nemotron category); ambiguous adapters stay refused.",
        ),
    ] = None,
    batch_records: Annotated[
        int,
        typer.Option(
            "--batch-records",
            help="Streaming batch size; performance only, never output semantics.",
        ),
    ] = 512,
    max_input_bytes: Annotated[
        int,
        typer.Option(
            "--max-input-bytes",
            help="Bounded streaming input byte budget (default 64 MiB).",
        ),
    ] = 64 * 1024 * 1024,
    output_shard_bytes: Annotated[
        int | None,
        typer.Option(
            "--output-shard-bytes",
            help="Shard canonical output by target serialized bytes "
            "(deterministic dataset manifest); omit for legacy single file.",
        ),
    ] = None,
) -> None:
    """Adapt verified selected records into CanonicalDocument JSONL via one adapter.

    Each input row must carry its ``_xlm_acquisition`` locator proving it was
    selected under this exact plan (revision, file, and selection hash are
    re-checked); rows from any other selection are refused, never coerced.
    """
    import time

    from xlm.data.acquisition.records import StreamingJsonlWriter
    from xlm.data.adapters.mix01_adapters import ADAPTERS_BY_ID, RecordRejectedError
    from xlm.data.adapters.rejections import (
        DOCUMENTS_FILENAME,
        REJECTIONS_FILENAME,
        SUMMARY_FILENAME,
        StagedAdaptation,
        build_rejection_record,
        build_summary,
        serialize_document,
        serialize_rejection,
    )
    from xlm.data.datasets.shards import (
        MANIFEST_FILENAME,
        ShardedJsonlWriter,
        input_byte_size,
        iter_input_blocks,
    )

    if on_reject not in ("fail", "record"):
        typer.echo("Error: --on-reject must be 'fail' or 'record'.", err=True)
        raise typer.Exit(code=1)
    if batch_records < 1:
        typer.echo("Error: --batch-records must be positive.", err=True)
        raise typer.Exit(code=1)
    if max_input_bytes < 1:
        typer.echo("Error: --max-input-bytes must be positive.", err=True)
        raise typer.Exit(code=1)
    if output_shard_bytes is not None and output_shard_bytes < 1:
        typer.echo("Error: --output-shard-bytes must be positive.", err=True)
        raise typer.Exit(code=1)

    try:
        plan = load_acquisition_plan(plan_path)
    except Exception as e:
        typer.echo(f"Error loading plan: {e}", err=True)
        raise typer.Exit(code=1) from e

    adapter_cls = ADAPTERS_BY_ID.get(adapter_id)
    if adapter_cls is None:
        typer.echo(
            f"Error: unknown adapter '{adapter_id}'. "
            f"Tested adapters: {', '.join(sorted(ADAPTERS_BY_ID))}.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        if adapter_config is None:
            adapter = adapter_cls()
        else:
            try:
                adapter = adapter_cls(adapter_config)
            except Exception as e:
                typer.echo(
                    f"Error: adapter '{adapter_id}' cannot apply --adapter-config "
                    f"'{adapter_config}' ({e}).",
                    err=True,
                )
                raise typer.Exit(code=1) from e
    except TypeError as e:
        typer.echo(
            f"Error: adapter '{adapter_id}' needs constructor parameters "
            f"({e}); it cannot be selected by 'data adapt' alone.",
            err=True,
        )
        raise typer.Exit(code=1) from e

    try:
        input_size = input_byte_size(input_path)
    except Exception as e:
        typer.echo(f"Error reading selected records: {e}", err=True)
        raise typer.Exit(code=1) from e
    if input_size > max_input_bytes:
        typer.echo(
            f"Error: selected records input exceeds limit "
            f"({input_size:,} > {max_input_bytes:,} bytes).",
            err=True,
        )
        raise typer.Exit(code=1)

    expected_selection = plan.accepted_selection_hashes()
    sharded = output_shard_bytes is not None
    staged: StagedAdaptation | None = None
    sharded_docs: ShardedJsonlWriter | None = None
    try:
        if sharded:
            assert output_shard_bytes is not None
            for legacy in (
                output_dir / DOCUMENTS_FILENAME,
                output_dir / REJECTIONS_FILENAME,
                output_dir / SUMMARY_FILENAME,
            ):
                if legacy.exists():
                    raise FileExistsError(
                        f"refusing to overwrite existing '{legacy}'; use a fresh output dir"
                    )
            sharded_docs = ShardedJsonlWriter(
                output_dir,
                dataset_id=plan.output_artifact_id,
                target_shard_bytes=output_shard_bytes,
                source_artifact={
                    "plan_id": plan.plan_id,
                    "plan_hash": plan.compute_behavioral_hash(),
                    "source_id": plan.source_id,
                    "view_id": plan.view_id,
                    "revision": plan.revision,
                },
                producer={
                    "adapter_id": adapter_id,
                    "on_reject": on_reject,
                    "output_shard_bytes": output_shard_bytes,
                    "tool": "xlm-data-adapt",
                },
            )
        else:
            if (output_dir / MANIFEST_FILENAME).exists():
                raise FileExistsError(
                    f"refusing to mix legacy output with published dataset manifest "
                    f"'{output_dir / MANIFEST_FILENAME}'; use a fresh output dir"
                )
            staged = StagedAdaptation(output_dir, with_ledger=(on_reject == "record"))
    except FileExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    total_input_records = 0
    line_number = 0
    input_bytes = 0
    parse_seconds = adapter_seconds = serialize_seconds = rejection_seconds = 0.0
    rejection_counts: dict[str, int] = {}
    pending: list[tuple[int, bytes]] = []
    buffer = bytearray()
    wall_start = time.monotonic()

    def run_batch(
        batch: list[tuple[int, bytes]],
        write_document: Any,
        write_rejection: Any,
    ) -> None:
        """Adapt one bounded batch; fatal errors abort via typer.Exit."""
        nonlocal total_input_records
        nonlocal parse_seconds
        nonlocal adapter_seconds
        nonlocal serialize_seconds
        nonlocal rejection_seconds
        for current_line, raw in batch:
            if not raw.strip():
                continue
            started = time.monotonic()
            try:
                record = json.loads(raw.decode("utf-8"))
            except Exception as e:
                typer.echo(f"Error: line {current_line} is not a JSON object: {e}", err=True)
                raise typer.Exit(code=1) from e
            parse_seconds += time.monotonic() - started
            if not isinstance(record, dict):
                typer.echo(f"Error: line {current_line} is not a JSON object.", err=True)
                raise typer.Exit(code=1)
            locator = record.get("_xlm_acquisition")
            if not isinstance(locator, dict):
                typer.echo(
                    f"Error: line {current_line} carries no _xlm_acquisition locator; "
                    "only verified selected records can be adapted.",
                    err=True,
                )
                raise typer.Exit(code=1)
            for key, expected in (
                ("source_id", plan.source_id),
                ("revision", plan.revision),
                ("source_file", None),
            ):
                actual = locator.get(key)
                if key == "source_file":
                    if actual not in plan.selected_files:
                        typer.echo(
                            f"Error: line {current_line} selects file {actual!r}, "
                            "outside this plan's selected files.",
                            err=True,
                        )
                        raise typer.Exit(code=1)
                elif actual != expected:
                    typer.echo(
                        f"Error: line {current_line} locator {key} {actual!r} does not "
                        f"match this plan ({expected!r}).",
                        err=True,
                    )
                    raise typer.Exit(code=1)
            if locator.get("selection_hash") not in expected_selection:
                typer.echo(
                    f"Error: line {current_line} locator selection_hash "
                    f"{locator.get('selection_hash')!r} does not match this plan.",
                    err=True,
                )
                raise typer.Exit(code=1)
            row_index = locator.get("row_index")
            if not isinstance(row_index, int) or row_index < 0:
                typer.echo(f"Error: line {current_line} locator has no valid row_index.", err=True)
                raise typer.Exit(code=1)
            started = time.monotonic()
            try:
                doc = adapter.adapt(
                    record,
                    source_file=str(locator["source_file"]),
                    source_row=row_index,
                    source_revision=plan.revision,
                )
            except RecordRejectedError as e:
                adapter_seconds += time.monotonic() - started
                if on_reject != "record":
                    typer.echo(
                        f"Error: adapter '{adapter_id}' refused line {current_line}: {e}",
                        err=True,
                    )
                    raise typer.Exit(code=1) from e
                started = time.monotonic()
                write_rejection(
                    serialize_rejection(
                        build_rejection_record(
                            input_line=current_line,
                            source_id=plan.source_id,
                            source_revision=plan.revision,
                            source_file=str(locator["source_file"]),
                            source_row=row_index,
                            adapter_id=adapter_id,
                            error=e,
                            original_record_sha256=locator.get("original_record_sha256")
                            if isinstance(locator.get("original_record_sha256"), str)
                            else None,
                        )
                    ),
                    type(e).__name__,
                )
                rejection_seconds += time.monotonic() - started
                code = type(e).__name__
                rejection_counts[code] = rejection_counts.get(code, 0) + 1
                total_input_records += 1
                continue
            except Exception as e:
                typer.echo(
                    f"Error: adapter '{adapter_id}' refused line {current_line}: {e}",
                    err=True,
                )
                raise typer.Exit(code=1) from e
            adapter_seconds += time.monotonic() - started
            if doc.source_id != plan.source_id:
                typer.echo(
                    f"Error: adapter '{adapter_id}' produced source '{doc.source_id}', "
                    f"not this plan's '{plan.source_id}'.",
                    err=True,
                )
                raise typer.Exit(code=1)
            started = time.monotonic()
            write_document(serialize_document(doc), doc.doc_id)
            serialize_seconds += time.monotonic() - started
            total_input_records += 1

    ledger_writer: StreamingJsonlWriter | None = None
    ledger_temp: Path | None = None
    ledger_count = 0

    def legacy_write_document(line: str, doc_id: str) -> None:
        if staged is None:
            raise ValueError("legacy sink without staged outputs")
        staged.write_document_line(line)

    def legacy_write_rejection(line: str, code: str) -> None:
        if staged is None:
            raise ValueError("legacy sink without staged outputs")
        staged.write_rejection_line(line, code)

    def sharded_write_document(line: str, doc_id: str) -> None:
        if sharded_docs is None:
            raise ValueError("sharded sink without a dataset writer")
        sharded_docs.write_line(line, doc_id)

    def sharded_write_rejection(line: str, code: str) -> None:
        nonlocal ledger_writer, ledger_temp, ledger_count
        if ledger_writer is None:
            output_dir.mkdir(parents=True, exist_ok=True)
            ledger_temp = output_dir / f"{REJECTIONS_FILENAME}.{uuid.uuid4().hex}.tmp"
            ledger_writer = StreamingJsonlWriter(ledger_temp)
        data = line.encode() + b"\n"
        assert ledger_writer is not None
        ledger_writer.write_raw(data)
        ledger_count += 1

    if sharded:
        write_document = sharded_write_document
        write_rejection = sharded_write_rejection
    else:
        write_document = legacy_write_document
        write_rejection = legacy_write_rejection

    def abort_outputs() -> None:
        if staged is not None:
            staged.abort()
        if sharded_docs is not None:
            sharded_docs.abandon()
        if ledger_writer is not None:
            try:
                ledger_writer.close()
            except Exception:
                pass
        if ledger_temp is not None:
            ledger_temp.unlink(missing_ok=True)

    try:
        for chunk, _shard_id, _ordinal in iter_input_blocks(input_path):
            input_bytes += len(chunk)
            if input_bytes > max_input_bytes:
                typer.echo(
                    f"Error: selected records input exceeds limit "
                    f"({input_bytes:,} > {max_input_bytes:,} bytes).",
                    err=True,
                )
                raise typer.Exit(code=1)
            buffer += chunk
            *lines, remainder = bytes(buffer).split(b"\n")
            buffer = bytearray(remainder)
            for raw in lines:
                line_number += 1
                if not raw.strip():
                    continue
                pending.append((line_number, raw))
                if len(pending) >= batch_records:
                    run_batch(pending, write_document, write_rejection)
                    pending = []
        if buffer:
            line_number += 1
            if bytes(buffer).strip():
                pending.append((line_number, bytes(buffer)))
            buffer = bytearray()
        if pending:
            run_batch(pending, write_document, write_rejection)
            pending = []
    except typer.Exit:
        abort_outputs()
        raise
    except Exception as e:
        abort_outputs()
        typer.echo(f"Error: adaptation failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    wall_seconds = max(0.0, time.monotonic() - wall_start)
    if sharded:
        assert sharded_docs is not None
        manifest_started = time.monotonic()
        manifest = sharded_docs.finish()
        manifest_seconds = max(0.0, time.monotonic() - manifest_started)
        if ledger_writer is None:
            output_dir.mkdir(parents=True, exist_ok=True)
            ledger_temp = output_dir / f"{REJECTIONS_FILENAME}.{uuid.uuid4().hex}.tmp"
            ledger_writer = StreamingJsonlWriter(ledger_temp)
        assert ledger_temp is not None
        ledger_writer.close()
        ledger_digest = ledger_writer.digest.hexdigest()
        accepted_records = sum(entry.doc_count for entry in manifest.shards)
        rejected_records = ledger_count
        manifest_sha = hashlib.sha256(
            (json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n").encode()
        ).hexdigest()
        sharded_summary = {
            "adaptation_summary_version": 1,
            "adapter_id": adapter_id,
            "source_id": plan.source_id,
            "source_revision": plan.revision,
            "plan_id": plan.plan_id,
            "plan_hash": plan.compute_behavioral_hash(),
            "on_reject": on_reject,
            "total_input_records": total_input_records,
            "accepted_records": accepted_records,
            "rejected_records": rejected_records,
            "rejection_counts_by_code": dict(sorted(rejection_counts.items())),
            "max_input_bytes": max_input_bytes,
            "output_shard_bytes": output_shard_bytes,
            "documents": {
                "count": accepted_records,
                "bytes": manifest.total_bytes,
                "aggregate_sha256": manifest.aggregate_sha256,
            },
            "manifest": {"file": MANIFEST_FILENAME, "sha256": manifest_sha},
            "shards": [entry.to_dict() for entry in manifest.shards],
            "rejections": {
                "file": REJECTIONS_FILENAME,
                "count": rejected_records,
                "sha256": ledger_digest,
            },
        }
        summary_temp = output_dir / f"{SUMMARY_FILENAME}.{uuid.uuid4().hex}.tmp"
        try:
            with summary_temp.open("xb") as stream:
                stream.write(
                    (json.dumps(sharded_summary, indent=2, sort_keys=True) + "\n").encode()
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(ledger_temp, output_dir / REJECTIONS_FILENAME)
            os.replace(summary_temp, output_dir / SUMMARY_FILENAME)
        finally:
            ledger_temp.unlink(missing_ok=True)
            summary_temp.unlink(missing_ok=True)
        published_docs = str(output_dir)
        throughput = total_input_records / wall_seconds if wall_seconds > 0 else 0.0
        throughput_mib = (input_bytes / (1024**2)) / wall_seconds if wall_seconds > 0 else 0.0
        oversize = sum(1 for entry in manifest.shards if entry.oversize)
        typer.echo(
            f"Adapted {accepted_records} record(s) via '{adapter_id}' to: {published_docs} "
            f"({len(manifest.shards)} shards, {rejected_records} rejection(s))"
        )
        typer.echo(
            f"Adapt throughput: {total_input_records} records in {wall_seconds:.2f}s "
            f"({throughput:.1f}/s, {throughput_mib:.3f} MiB/s input, batch {batch_records}, "
            f"parse {parse_seconds:.2f}s adapt {adapter_seconds:.2f}s "
            f"serialize {serialize_seconds:.2f}s write {sharded_docs.flush_seconds:.2f}s "
            f"manifest {manifest_seconds:.2f}s shards {len(manifest.shards)} "
            f"oversize {oversize} peak_rss {sharded_docs.peak_rss_bytes:,}B)"
        )
        return
    assert staged is not None
    accepted_records = staged.accepted_records
    rejected_records = staged.rejected_records
    summary: dict[str, Any] | None = None
    if on_reject == "record":
        summary = build_summary(
            adapter_id=adapter_id,
            source_id=plan.source_id,
            source_revision=plan.revision,
            plan_id=plan.plan_id,
            plan_hash=plan.compute_behavioral_hash(),
            on_reject=on_reject,
            total_input_records=total_input_records,
            accepted_records=accepted_records,
            rejected_records=rejected_records,
            rejection_counts_by_code=dict(rejection_counts),
            document_sha256=staged.document_digest,
            rejection_sha256=staged.rejection_digest,
            max_input_bytes=max_input_bytes,
            output_shard_bytes=output_shard_bytes,
        )
    try:
        published = staged.finish(summary)
    except FileExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    throughput = total_input_records / wall_seconds if wall_seconds > 0 else 0.0
    throughput_mib = (input_bytes / (1024**2)) / wall_seconds if wall_seconds > 0 else 0.0
    if on_reject == "fail":
        typer.echo(
            f"Adapted {accepted_records} record(s) via '{adapter_id}' to: "
            f"{published[DOCUMENTS_FILENAME]}"
        )
    else:
        typer.echo(
            f"Adapted {accepted_records} record(s) via '{adapter_id}' to: "
            f"{published[DOCUMENTS_FILENAME]} "
            f"({rejected_records} rejection(s) in {published[REJECTIONS_FILENAME]}, "
            f"summary in {published[SUMMARY_FILENAME]})"
        )
    typer.echo(
        f"Adapt throughput: {total_input_records} records in {wall_seconds:.2f}s "
        f"({throughput:.1f}/s, {throughput_mib:.3f} MiB/s input, batch {batch_records}, "
        f"parse {parse_seconds:.2f}s adapt {adapter_seconds:.2f}s "
        f"serialize {serialize_seconds:.2f}s write {staged.flush_seconds:.2f}s)"
    )


@app.command("clean")
def clean_cmd(
    input_path: Annotated[
        Path,
        typer.Option("--input", "-i", help="Path to input raw or canonical JSONL/Parquet dataset."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory to store cleaned canonical dataset."),
    ],
    preset: Annotated[
        str,
        typer.Option(
            "--preset",
            "-p",
            help=(
                "Cleaning preset: 'educational_prose', 'code', "
                "'historical_ocr' or 'structured_synthetic'."
            ),
        ),
    ] = "educational_prose",
    quarantine_dir: Annotated[
        Path | None,
        typer.Option("--quarantine-dir", help="Directory for quarantined records."),
    ] = None,
    max_docs: Annotated[
        int | None,
        typer.Option("--max-docs", help="Maximum documents to process (sample limit)."),
    ] = None,
    publish: Annotated[
        bool,
        typer.Option(
            "--publish/--no-publish", help="Publish clean_dataset artifact into ArtifactStore."
        ),
    ] = False,
    report: Annotated[
        bool,
        typer.Option(
            "--report/--no-report",
            help="Generate quality report automatically in output directory.",
        ),
    ] = True,
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            help="Cleaning worker processes (shard-level parallelism; "
            "outputs are identical for any count).",
        ),
    ] = 4,
    input_shard_bytes: Annotated[
        int,
        typer.Option(
            "--input-shard-bytes",
            help="Target byte size for splitting single-file input into "
            "deterministic work units (smaller balances workers better; "
            "below ~1 MiB per-unit overhead dominates).",
        ),
    ] = 16 * 1024 * 1024,
    output_shard_bytes: Annotated[
        int | None,
        typer.Option(
            "--output-shard-bytes",
            help="Shard accepted output by target serialized bytes "
            "(deterministic clean manifest); omit for legacy single file.",
        ),
    ] = None,
    quarantine_shard_bytes: Annotated[
        int | None,
        typer.Option(
            "--quarantine-shard-bytes",
            help="Shard quarantine output by target bytes; omit for a single quarantine.jsonl.",
        ),
    ] = None,
    max_input_bytes: Annotated[
        int,
        typer.Option(
            "--max-input-bytes",
            help="Refuse clean inputs larger than this budget (default 2 GiB).",
        ),
    ] = 2 * 1024 * 1024 * 1024,
) -> None:
    """Execute composable data cleaning and filtering pipeline adhering to C01, C02, and C03.

    Canonical input (a ``documents.jsonl`` file, a verified P27A shard
    manifest directory, a plain directory of JSONL shards, or Parquet) runs
    through the deterministic sharded cleaning engine: independent work
    units, one pipeline per worker process, ordered assembly into legacy
    single-file or sharded output. Raw (non-canonical) single-file input
    keeps the original streaming orchestration unchanged.
    """
    if not input_path.exists():
        typer.echo(f"Error: Input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)
    if workers < 1:
        typer.echo("Error: --workers must be positive.", err=True)
        raise typer.Exit(code=1)
    if input_shard_bytes < 1:
        typer.echo("Error: --input-shard-bytes must be positive.", err=True)
        raise typer.Exit(code=1)
    if output_shard_bytes is not None and output_shard_bytes < 1:
        typer.echo("Error: --output-shard-bytes must be positive.", err=True)
        raise typer.Exit(code=1)
    if quarantine_shard_bytes is not None and quarantine_shard_bytes < 1:
        typer.echo("Error: --quarantine-shard-bytes must be positive.", err=True)
        raise typer.Exit(code=1)
    if max_input_bytes < 1:
        typer.echo("Error: --max-input-bytes must be positive.", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    q_dir = quarantine_dir or (output_dir / "quarantine")

    try:
        create_pipeline_preset(preset)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if _clean_input_is_raw(input_path):
        _run_legacy_raw_clean(input_path, output_dir, preset, q_dir, max_docs, publish, report)
        return

    policy = QuarantinePolicy()
    try:
        summary, timing, assembled, throughput, _manifest = run_sharded_clean(
            input_path=input_path,
            output_dir=output_dir,
            preset=preset,
            workers=workers,
            input_shard_bytes=input_shard_bytes,
            output_shard_bytes=output_shard_bytes,
            quarantine_dir=q_dir,
            quarantine_shard_bytes=quarantine_shard_bytes,
            quarantine_policy=policy,
            max_docs=max_docs,
            max_input_bytes=max_input_bytes,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError, TypeError) as e:
        typer.echo(f"Error cleaning documents: {e}", err=True)
        raise typer.Exit(code=1) from e
    except RuntimeError as e:
        typer.echo(f"Error cleaning documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    if output_shard_bytes is None and HAS_PYARROW:
        writer = CanonicalDatasetWriter(output_dir)
        try:
            writer.write_parquet_stream(
                CanonicalDatasetReader.read_jsonl(output_dir / "documents.jsonl"),
                filename="documents.parquet",
            )
        except Exception as e:
            typer.echo(f"Error writing Parquet view: {e}", err=True)
            raise typer.Exit(code=1) from e
    elif output_shard_bytes is not None:
        typer.echo("Note: sharded clean output carries no Parquet view (manifest + shards).")
    else:
        typer.echo("Note: pyarrow unavailable; wrote JSONL only (no Parquet view).")

    summary_path = output_dir / "cleaning_summary.json"

    reporter = QualityReporter(summary)
    if report:
        md_p, html_p = reporter.save_reports(output_dir)
        typer.echo(f"Quality report generated: {md_p} and {html_p}")

    if publish and summary.completed and not summary.is_partial_sample:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        art_id = f"clean_{preset}_{summary.pipeline_hash[:12]}"
        files_to_publish: dict[str, Path] = {
            "cleaning_summary.json": summary_path,
        }
        for name in assembled.accepted_files:
            files_to_publish[name] = output_dir / name
        if assembled.manifest_file is not None:
            files_to_publish[assembled.manifest_file] = output_dir / assembled.manifest_file
        if (output_dir / "documents.parquet").exists():
            files_to_publish["documents.parquet"] = output_dir / "documents.parquet"
        if (output_dir / "quality_report.md").exists():
            files_to_publish["quality_report.md"] = output_dir / "quality_report.md"

        published_dir = store.publish_artifact(
            artifact_id=art_id,
            kind="clean_dataset",
            files=files_to_publish,
            producer_code_hash=compute_sha256("xlm.cli.data_cmd:clean_cmd")[:16],
            dependency_hash=compute_sha256("uv.lock")[:16],
            resolved_config_hash=summary.pipeline_hash,
            metadata={
                "domain_preset": preset,
                "document_yield_ratio": summary.document_yield_ratio,
                "byte_yield_ratio": summary.byte_yield_ratio,
                "total_output_docs": summary.total_output_docs,
            },
        )
        typer.echo(f"Successfully published clean_dataset artifact to: {published_dir}")

    typer.echo("============================================================")
    typer.echo(f"Pipeline Completed: {preset} (Hash: {summary.pipeline_hash[:16]}...)")
    typer.echo(
        f"Input:       {summary.total_input_docs:,} docs ({summary.total_input_bytes:,} bytes)"
    )
    typer.echo(
        f"Retained:    {summary.total_output_docs:,} docs ({summary.total_output_bytes:,} bytes)"
    )
    typer.echo(
        f"Yield:       {summary.document_yield_ratio * 100:.1f}% docs "
        f"({summary.byte_yield_ratio * 100:.1f}% bytes)"
    )
    typer.echo(f"Rejected:    {summary.total_rejected_docs:,} docs")
    typer.echo(f"Quarantine:  {throughput['quarantine_records']:,} records stored in {q_dir}")
    typer.echo(f"Output:      {output_dir}")
    typer.echo(
        f"Clean throughput: {summary.total_input_docs:,} docs in "
        f"{throughput['wall_seconds']:.2f}s ({throughput['docs_per_second']:,.0f}/s, "
        f"{throughput['input_mib_per_second']:.2f} MiB/s in, "
        f"{throughput['output_mib_per_second']:.2f} MiB/s out, "
        f"workers {throughput['workers']}, input_shards {throughput['input_shards']}, "
        f"output_shards {throughput['output_shards']})"
    )
    stage_parts = ", ".join(
        f"{name} {secs:.2f}s" for name, secs in throughput["stage_seconds"].items()
    )
    typer.echo(
        f"Stage seconds: parse {throughput['parse_seconds']:.2f}s, {stage_parts}, "
        f"serialize {throughput['serialization_seconds']:.2f}s, "
        f"write {throughput['accepted_write_seconds']:.2f}s, "
        f"quarantine {throughput['quarantine_seconds']:.2f}s, "
        f"fsync {throughput['fsync_seconds']:.2f}s"
    )
    typer.echo("============================================================")


def _clean_input_is_raw(input_path: Path) -> bool:
    """True when a single input file does not hold canonical records."""
    if input_path.is_dir() or input_path.suffix == ".parquet":
        return False
    try:
        docs_iter = CanonicalDatasetReader.read_jsonl(input_path)
        first = next(docs_iter, None)
        if first is not None:
            return False
        return True
    except (ValueError, TypeError):
        return True


def _run_legacy_raw_clean(
    input_path: Path,
    output_dir: Path,
    preset: str,
    q_dir: Path,
    max_docs: int | None,
    publish: bool,
    report: bool,
) -> None:
    """Clean raw (non-canonical) single-file input with the original orchestration."""
    quarantine_mgr = QuarantineManager(q_dir)
    pipeline = create_pipeline_preset(preset)
    adapter = JsonlAdapter(source_id="raw_input")
    docs = adapter.process_file(input_path)

    typer.echo(f"Starting cleaning pipeline with preset '{preset}'...")
    accepted_iter, summary = pipeline.run_stream(
        documents=docs,
        quarantine_mgr=quarantine_mgr,
        max_docs=max_docs,
    )

    writer = CanonicalDatasetWriter(output_dir)
    jsonl_path = writer.write_jsonl(accepted_iter, filename="documents.jsonl")

    if HAS_PYARROW:
        writer.write_parquet_stream(
            CanonicalDatasetReader.read_jsonl(jsonl_path),
            filename="documents.parquet",
        )
    else:
        typer.echo("Note: pyarrow unavailable; wrote JSONL only (no Parquet view).")

    summary_path = output_dir / "cleaning_summary.json"
    summary_path.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")

    reporter = QualityReporter(summary)
    if report:
        md_p, html_p = reporter.save_reports(output_dir)
        typer.echo(f"Quality report generated: {md_p} and {html_p}")

    if publish and summary.completed and not summary.is_partial_sample:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        art_id = f"clean_{preset}_{summary.pipeline_hash[:12]}"
        files_to_publish: dict[str, Path] = {
            "documents.jsonl": output_dir / "documents.jsonl",
            "cleaning_summary.json": summary_path,
        }
        if (output_dir / "documents.parquet").exists():
            files_to_publish["documents.parquet"] = output_dir / "documents.parquet"
        if (output_dir / "quality_report.md").exists():
            files_to_publish["quality_report.md"] = output_dir / "quality_report.md"

        published_dir = store.publish_artifact(
            artifact_id=art_id,
            kind="clean_dataset",
            files=files_to_publish,
            producer_code_hash=compute_sha256("xlm.cli.data_cmd:clean_cmd")[:16],
            dependency_hash=compute_sha256("uv.lock")[:16],
            resolved_config_hash=summary.pipeline_hash,
            metadata={
                "domain_preset": preset,
                "document_yield_ratio": summary.document_yield_ratio,
                "byte_yield_ratio": summary.byte_yield_ratio,
                "total_output_docs": summary.total_output_docs,
            },
        )
        typer.echo(f"Successfully published clean_dataset artifact to: {published_dir}")

    typer.echo("============================================================")
    typer.echo(f"Pipeline Completed: {preset} (Hash: {summary.pipeline_hash[:16]}...)")
    typer.echo(
        f"Input:       {summary.total_input_docs:,} docs ({summary.total_input_bytes:,} bytes)"
    )
    typer.echo(
        f"Retained:    {summary.total_output_docs:,} docs ({summary.total_output_bytes:,} bytes)"
    )
    typer.echo(
        f"Yield:       {summary.document_yield_ratio * 100:.1f}% docs "
        f"({summary.byte_yield_ratio * 100:.1f}% bytes)"
    )
    typer.echo(f"Rejected:    {summary.total_rejected_docs:,} docs")
    typer.echo(f"Quarantine:  {quarantine_mgr.recorded_count:,} records stored in {q_dir}")
    typer.echo(f"Output:      {output_dir}")
    typer.echo("============================================================")


@app.command("quality-report")
def quality_report_cmd(
    report_dir: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Directory containing cleaning execution summary."),
    ],
    format_type: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format ('markdown', 'html', or 'terminal')."),
    ] = "terminal",
    output_file: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output report file path."),
    ] = None,
) -> None:
    """Inspect or regenerate quality reports for a cleaned dataset."""

    summary_p = report_dir / "cleaning_summary.json"
    if not summary_p.exists():
        typer.echo(f"Error: cleaning_summary.json not found in '{report_dir}'.", err=True)
        raise typer.Exit(code=1)

    data = json.loads(summary_p.read_text(encoding="utf-8"))
    stage_stats = [
        StageStats(
            stage_name=st["stage_name"],
            transform_id=st["transform_id"],
            transform_version=st["transform_version"],
            input_docs=st["input_docs"],
            output_docs=st["output_docs"],
            input_bytes=st["input_bytes"],
            output_bytes=st["output_bytes"],
            rejected_docs=st["rejected_docs"],
            duration_ms=st["duration_ms"],
        )
        for st in data.get("stage_metrics", [])
    ]

    summary = PipelineExecutionSummary(
        pipeline_hash=data["pipeline_hash"],
        domain_preset=data["domain_preset"],
        total_input_docs=data["total_input_docs"],
        total_output_docs=data["total_output_docs"],
        total_input_bytes=data["total_input_bytes"],
        total_output_bytes=data["total_output_bytes"],
        total_rejected_docs=data["total_rejected_docs"],
        document_yield_ratio=data["document_yield_ratio"],
        byte_yield_ratio=data["byte_yield_ratio"],
        stage_metrics=stage_stats,
        reason_counts=data.get("reason_counts", {}),
        elapsed_seconds=data["elapsed_seconds"],
        is_partial_sample=data.get("is_partial_sample", False),
        declared_max_docs=data.get("declared_max_docs"),
    )

    reporter = QualityReporter(summary)
    fmt = format_type.lower()
    if fmt == "html":
        content = reporter.generate_html()
    elif fmt in ("markdown", "terminal"):
        content = reporter.generate_markdown()
    else:
        typer.echo(
            f"Unknown format: '{format_type}'. Supported: 'terminal', 'markdown', 'html'",
            err=True,
        )
        raise typer.Exit(code=1)

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        typer.echo(f"Report written to: {output_file}")
    else:
        typer.echo(content)


@app.command("dedup")
def dedup_cmd(
    input_path: Annotated[
        Path,
        typer.Option("--input", "-i", help="Canonical dataset file or shard directory."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for deduplicated output and reports."),
    ],
    work_dir: Annotated[
        Path | None,
        typer.Option("--work-dir", help="Scratch directory for disk-backed indexes."),
    ] = None,
    jaccard_threshold: Annotated[
        float,
        typer.Option("--jaccard-threshold", help="Frozen near-duplicate similarity threshold."),
    ] = 0.8,
    partitions: Annotated[
        int,
        typer.Option(
            "--partitions", help="On-disk index partitions (IO only; never changes results)."
        ),
    ] = 16,
    near_duplicates: Annotated[
        bool,
        typer.Option("--near/--exact-only", help="Enable the near-duplicate pass."),
    ] = True,
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            help="Dedup worker processes for signature generation "
            "(results are identical for any count).",
        ),
    ] = 4,
    input_shard_bytes: Annotated[
        int,
        typer.Option(
            "--input-shard-bytes",
            help="Target byte size for splitting single-file input into deterministic work units.",
        ),
    ] = 16 * 1024 * 1024,
    output_shard_bytes: Annotated[
        int | None,
        typer.Option(
            "--output-shard-bytes",
            help="Shard survivor output by target serialized bytes "
            "(deterministic manifest); omit for legacy single file.",
        ),
    ] = None,
    max_input_bytes: Annotated[
        int,
        typer.Option(
            "--max-input-bytes",
            help="Refuse dedup inputs larger than this budget (default 2 GiB).",
        ),
    ] = 2 * 1024 * 1024 * 1024,
) -> None:
    """Deduplicate canonical documents across source families (C05).

    Consumes a ``documents.jsonl`` file, a verified clean shard manifest, a
    plain directory of JSONL shards, or Parquet — streaming, never
    materialized. The lexical exact/MinHash policy is unchanged; survivors
    stream to legacy single-file or sharded output with identical decisions.
    """
    if not input_path.exists():
        typer.echo(f"Error: input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)
    if workers < 1:
        typer.echo("Error: --workers must be positive.", err=True)
        raise typer.Exit(code=1)
    if input_shard_bytes < 1:
        typer.echo("Error: --input-shard-bytes must be positive.", err=True)
        raise typer.Exit(code=1)
    if output_shard_bytes is not None and output_shard_bytes < 1:
        typer.echo("Error: --output-shard-bytes must be positive.", err=True)
        raise typer.Exit(code=1)
    if max_input_bytes < 1:
        typer.echo("Error: --max-input-bytes must be positive.", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    scratch = work_dir or (output_dir / "dedup_work")

    try:
        config = DedupConfig(
            minhash=MinHashConfig(jaccard_threshold=jaccard_threshold),
            partition_count=partitions,
            enable_near_duplicates=near_duplicates,
        )
    except ValueError as e:
        typer.echo(f"Error: invalid dedup configuration: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Deduplicating input (identity {config.identity()})...")
    try:
        result, telemetry, assembled, throughput, _manifest = run_sharded_dedup(
            input_path=input_path,
            output_dir=output_dir,
            config=config,
            workers=workers,
            input_shard_bytes=input_shard_bytes,
            output_shard_bytes=output_shard_bytes,
            work_dir=scratch,
            max_input_bytes=max_input_bytes,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError, TypeError) as e:
        typer.echo(f"Error deduplicating documents: {e}", err=True)
        raise typer.Exit(code=1) from e
    except RuntimeError as e:
        typer.echo(f"Error deduplicating documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    if output_shard_bytes is None and HAS_PYARROW:
        writer = CanonicalDatasetWriter(output_dir)
        try:
            writer.write_parquet_stream(
                CanonicalDatasetReader.read_jsonl(output_dir / "documents.jsonl"),
                filename="documents.parquet",
            )
        except Exception as e:
            typer.echo(f"Error writing Parquet view: {e}", err=True)
            raise typer.Exit(code=1) from e
    elif output_shard_bytes is not None:
        typer.echo("Note: sharded dedup output carries no Parquet view (manifest + shards).")
    else:
        typer.echo("Note: pyarrow unavailable; wrote JSONL only (no Parquet view).")

    typer.echo("============================================================")
    typer.echo(f"Dedup identity:  {result.config_identity}")
    typer.echo(f"Input:           {result.stats.documents_seen:,} documents")
    typer.echo(f"Survivors:       {len(result.survivor_doc_ids):,}")
    typer.echo(f"Dropped:         {len(result.dropped_doc_ids):,}")
    typer.echo(f"Clusters:        {len(result.clusters):,}")
    typer.echo(f"Exact pairs:     {result.stats.exact_duplicate_pairs:,}")
    typer.echo(f"Near confirmed:  {result.stats.near_duplicate_pairs_confirmed:,}")
    typer.echo(f"Oversized buckets skipped: {result.stats.oversized_buckets:,}")
    typer.echo("Near-duplicate detection has false positives and negatives; it does not")
    typer.echo("prove the corpus is free of paraphrased or restructured duplicates.")
    typer.echo(
        f"Dedup throughput: {result.stats.documents_seen:,} docs in "
        f"{throughput['wall_seconds']:.2f}s "
        f"({result.stats.documents_seen / max(1e-9, throughput['wall_seconds']):,.0f}/s, "
        f"workers {throughput['workers']}, input_shards {throughput['input_shards']}, "
        f"output_shards {throughput['output_shards']}, "
        f"signatures {throughput['signatures_per_second']:,.0f}/s)"
    )
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")

    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


@app.command("embeddings-build")
def embeddings_build_cmd(
    input_path: Annotated[
        Path,
        typer.Option("--input", "-i", help="Clean documents.jsonl or shard manifest directory."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for the embedding artifact."),
    ],
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="Test/synthetic provider only: 'synthetic' or 'texthash'. "
            "Real encoders are operator-supplied; nothing is downloaded.",
        ),
    ] = "synthetic",
    dim: Annotated[int, typer.Option("--dim", help="Embedding dimension.")] = 384,
    seed: Annotated[int, typer.Option("--seed", help="Synthetic provider seed.")] = 20260919,
    dtype: Annotated[str, typer.Option("--dtype", help="float32 or float16.")] = "float32",
    vectors_per_shard: Annotated[
        int, typer.Option("--vectors-per-shard", help="Rows per .npy shard.")
    ] = 65536,
    batch_size: Annotated[int, typer.Option("--batch-size", help="Encode batch size.")] = 1024,
) -> None:
    """Build a versioned precomputed-embedding artifact (test providers only).

    Vectors are written as contiguous ``.npy`` shards plus an
    ``embedding-manifest.json``. Requires NumPy in the operator environment;
    fails clearly otherwise. No model is downloaded, ever.
    """
    from xlm.data.semantic.embeddings import (
        EMBEDDING_MANIFEST_FILENAME,
        EmbeddingManifest,
        EmbeddingShardRef,
        write_vector_shard,
    )
    from xlm.data.semantic.providers import SyntheticEmbeddingProvider, TextHashEmbeddingProvider

    if not input_path.exists():
        typer.echo(f"Error: input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)
    if dtype not in ("float32", "float16"):
        typer.echo("Error: --dtype must be 'float32' or 'float16'.", err=True)
        raise typer.Exit(code=1)
    if vectors_per_shard < 1 or batch_size < 1 or dim < 1:
        typer.echo(
            "Error: --vectors-per-shard, --batch-size, and --dim must be positive.", err=True
        )
        raise typer.Exit(code=1)
    try:
        import numpy as np  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        typer.echo(
            "Error: embeddings-build needs NumPy, which is absent here; run in an "
            "operator environment with numpy installed.",
            err=True,
        )
        raise typer.Exit(code=1) from None

    if provider == "synthetic":
        encoder: Any = SyntheticEmbeddingProvider(dim=dim, dtype=dtype, seed=seed)
    elif provider == "texthash":
        encoder = TextHashEmbeddingProvider(dim=dim, dtype=dtype)
    else:
        typer.echo(
            f"Error: unknown provider '{provider}'; want 'synthetic' or 'texthash'.", err=True
        )
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        units, input_info = _plan_dedup_units(input_path)
    except (FileNotFoundError, NotADirectoryError, ValueError, TypeError) as e:
        typer.echo(f"Error loading input documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    source_artifact = {
        "input_kind": input_info["kind"],
        "total_documents": input_info["total_documents"],
        "total_bytes": input_info["total_bytes"],
    }
    if input_info["kind"] == "manifest":
        source_artifact["clean_aggregate_sha256"] = input_info.get("aggregate_sha256")

    manifest = EmbeddingManifest(
        source_artifact=source_artifact,
        model_identity=encoder.model_identity,
        dim=dim,
        dtype=dtype,
        normalized=encoder.normalized,
        truncation_policy=encoder.truncation_policy,
    )
    shard_index = 0
    pending_texts: list[str] = []
    pending_ids: list[str] = []

    def flush_shard() -> None:
        nonlocal shard_index
        if not pending_ids:
            return
        import numpy as _np

        # Batches bound provider memory; concatenation order is document
        # order, so batch boundaries never affect vectors or layout.
        encoded = [
            _np.ascontiguousarray(encoder.encode(pending_texts[i : i + batch_size]))
            for i in range(0, len(pending_texts), batch_size)
        ]
        array = _np.ascontiguousarray(
            _np.concatenate(encoded) if len(encoded) > 1 else encoded[0], dtype=dtype
        )
        path = output_dir / f"vectors-{shard_index:05d}.npy"
        digest = write_vector_shard(array, path)
        manifest.shards.append(
            EmbeddingShardRef(
                path=path.name,
                rows=int(array.shape[0]),
                dim=dim,
                dtype=dtype,
                sha256=digest,
            )
        )
        manifest.doc_ids.extend(pending_ids)
        manifest.total_vectors += int(array.shape[0])
        shard_index += 1
        pending_texts.clear()
        pending_ids.clear()

    try:
        for doc in _stream_dedup_unit_documents(input_path, units):
            pending_texts.append(doc.text)
            pending_ids.append(doc.doc_id)
            if len(pending_texts) >= vectors_per_shard:
                flush_shard()
        flush_shard()
    except (ValueError, TypeError, RuntimeError) as e:
        typer.echo(f"Error building embeddings: {e}", err=True)
        raise typer.Exit(code=1) from e

    manifest.vector_bytes = sum(
        (output_dir / shard.path).stat().st_size for shard in manifest.shards
    )
    manifest_path = output_dir / EMBEDDING_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    typer.echo("============================================================")
    typer.echo(f"Embeddings:      {manifest.total_vectors:,} vectors x {dim} ({dtype})")
    typer.echo(f"Model identity:  {manifest.model_identity}")
    typer.echo(f"Artifact identity: {manifest.identity()[:16]}...")
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


@app.command("embeddings-validate")
def embeddings_validate_cmd(
    artifact_dir: Annotated[
        Path, typer.Option("--dir", "-d", help="Embedding artifact directory.")
    ],
) -> None:
    """Validate an embedding artifact directory, failing closed on any mismatch."""
    from xlm.data.semantic.embeddings import validate_embedding_artifact

    try:
        manifest = validate_embedding_artifact(artifact_dir)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"Error: invalid embedding artifact: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(
        f"Valid embedding artifact: {manifest.total_vectors:,} x {manifest.dim} "
        f"({manifest.dtype}), identity {manifest.identity()[:16]}..."
    )


@app.command("semantic-neighbors")
def semantic_neighbors_cmd(
    embeddings_dir: Annotated[
        Path, typer.Option("--embeddings", "-e", help="Validated embedding artifact directory.")
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for the candidate sidecar."),
    ],
    backend: Annotated[
        str,
        typer.Option("--backend", help="auto, python, numpy, faiss-cpu, or faiss-gpu."),
    ] = "auto",
    top_k: Annotated[int, typer.Option("--top-k", help="Neighbors per query.")] = 10,
    threshold: Annotated[
        float, typer.Option("--threshold", help="Minimum cosine similarity.")
    ] = 0.9,
    query_batch_size: Annotated[
        int, typer.Option("--query-batch-size", help="Queries per search batch.")
    ] = 2048,
    cluster_policy: Annotated[
        str,
        typer.Option(
            "--semantic-cluster-policy",
            help="Candidate sidecar only ('none-v1', default) or versioned "
            "experimental threshold-union analysis ('v0-experimental-threshold-union'). "
            "Never affects dedup survivors.",
        ),
    ] = "none-v1",
) -> None:
    """Generate semantic-neighbor candidates (EXPERIMENTAL sidecar, OFF by default).

    FAISS/NumPy is candidate generation only: this command writes
    ``candidates.jsonl`` plus a threshold-sweep analysis. It never deletes
    documents and never feeds survivor decisions. Use the operator GPU
    benchmark as: ``xlm data semantic-neighbors --embeddings <dir>
    --backend faiss-gpu ...`` in an environment with faiss-gpu installed.
    """
    from xlm.data.semantic.backends import backend_metadata, resolve_backend
    from xlm.data.semantic.embeddings import validate_embedding_artifact
    from xlm.data.semantic.neighbors import (
        SEMANTIC_POLICY_NONE,
        SEMANTIC_POLICY_V0_EXPERIMENTAL,
        generate_candidates,
        summarize_thresholds,
    )

    if cluster_policy not in (SEMANTIC_POLICY_NONE, SEMANTIC_POLICY_V0_EXPERIMENTAL):
        typer.echo(f"Error: unknown --semantic-cluster-policy '{cluster_policy}'.", err=True)
        raise typer.Exit(code=1)
    if top_k < 1 or query_batch_size < 1:
        typer.echo("Error: --top-k and --query-batch-size must be positive.", err=True)
        raise typer.Exit(code=1)
    if not 0.0 <= threshold <= 1.0:
        typer.echo("Error: --threshold must lie in [0, 1].", err=True)
        raise typer.Exit(code=1)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest = validate_embedding_artifact(embeddings_dir)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        typer.echo(f"Error: invalid embedding artifact: {e}", err=True)
        raise typer.Exit(code=1) from e
    try:
        backend_instance = resolve_backend(backend)
    except (ValueError, RuntimeError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    try:
        vectors, ordinals = _load_embedding_vectors(embeddings_dir, manifest, backend_instance.name)
        artifact, telemetry = generate_candidates(
            backend=backend_instance,
            vectors=vectors,
            ordinals=ordinals,
            top_k=top_k,
            threshold=threshold,
            output_path=output_dir / "candidates.jsonl",
            query_batch_size=query_batch_size,
        )
    except (ValueError, RuntimeError) as e:
        typer.echo(f"Error generating candidates: {e}", err=True)
        raise typer.Exit(code=1) from e

    artifact.clean_artifact_identity = manifest.source_artifact
    artifact.embedding_identity = manifest.identity()
    artifact.policy = cluster_policy
    analysis = summarize_thresholds(output_dir / "candidates.jsonl")
    report = {
        "candidate_artifact": artifact.to_dict(),
        "candidate_identity": artifact.identity(),
        "telemetry": telemetry,
        "backend": backend_metadata(backend_instance),
        "threshold_analysis": analysis,
        "experimental_clusters": (
            _experimental_threshold_clusters(output_dir / "candidates.jsonl", threshold)
            if cluster_policy == SEMANTIC_POLICY_V0_EXPERIMENTAL
            else None
        ),
    }
    (output_dir / "candidates-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    typer.echo("============================================================")
    typer.echo(f"Backend:         {telemetry['backend']} (requested '{backend}')")
    typer.echo(f"Candidates:      {telemetry['candidate_count']:,}")
    typer.echo(f"Candidate identity: {artifact.identity()[:16]}...")
    typer.echo("NOTE: sidecar only — no survivor decision was made or changed.")
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


def _plan_dedup_units(
    input_path: Path,
) -> tuple[list[Any], dict[str, Any]]:
    """Plan dedup work units for any supported input shape."""
    from xlm.data.cleaning.sharded import plan_clean_units

    # Embeddings build streams single-process; coarse units minimize overhead.
    return plan_clean_units(input_path, input_shard_bytes=64 * 1024 * 1024, max_docs=None)


def _stream_dedup_unit_documents(input_path: Path, units: list[Any]) -> Any:
    """Yield documents in global input order across planned units."""
    from xlm.data.dedup.sharded import _iter_unit_documents

    for unit in units:
        for _position, doc in _iter_unit_documents(unit):
            yield doc


def _load_embedding_vectors(
    embeddings_dir: Path, manifest: Any, backend_name: str
) -> tuple[Any, list[int]]:
    """Load all vector shards in manifest order for the resolved backend.

    NumPy concatenation (one documented copy) when available; otherwise the
    stdlib row reader, which only the pure-Python backend accepts.
    """
    from xlm.data.semantic.embeddings import read_vector_rows_stdlib, read_vector_shard

    try:
        import numpy as _np

        have_numpy = True
    except ImportError:
        have_numpy = False
    if not have_numpy:
        if backend_name != "python":
            typer.echo(
                "Error: semantic-neighbors needs NumPy for this backend, which is "
                "absent here; use --backend python or run in an operator "
                "environment with numpy installed.",
                err=True,
            )
            raise typer.Exit(code=1)
        rows: list[list[float]] = []
        for shard in manifest.shards:
            rows.extend(read_vector_rows_stdlib(embeddings_dir / shard.path))
        if len(rows) != manifest.total_vectors:
            raise ValueError("concatenated vectors disagree with manifest totals")
        return rows, list(range(manifest.total_vectors))

    chunks = []
    for shard in manifest.shards:
        chunks.append(read_vector_shard(embeddings_dir / shard.path, memmap=True))
    matrix = _np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    ordinals = list(range(manifest.total_vectors))
    if matrix.shape[0] != manifest.total_vectors:
        raise ValueError("concatenated vectors disagree with manifest totals")
    return matrix, ordinals


def _experimental_threshold_clusters(candidate_path: Path, threshold: float) -> dict[str, Any]:
    """Versioned experimental threshold-union clustering (analysis only)."""
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        while parent.get(node, node) != node:
            parent[node] = parent.get(parent[node], parent[node])
            node = parent[node]
        return parent.get(node, node)

    members = 0
    with candidate_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            if float(record["score"]) < threshold:
                continue
            members += 1
            left, right = int(record["doc_a"]), int(record["doc_b"])
            rleft, rright = find(left), find(right)
            if rleft != rright:
                parent[rright] = rleft
    roots: dict[int, int] = {}
    for node in list(parent):
        root = find(node)
        roots[root] = roots.get(root, 0) + 1
    sizes = sorted(roots.values(), reverse=True)
    return {
        "policy": "v0-experimental-threshold-union",
        "threshold": threshold,
        "edges": members,
        "components": len(sizes),
        "largest_component_nodes": sizes[0] if sizes else 0,
    }


@app.command("split")
def split_cmd(
    input_path: Annotated[
        Path,
        typer.Option("--input", "-i", help="Deduplicated canonical dataset file or directory."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for split output and the pool freeze."),
    ],
    dedup_report: Annotated[
        Path | None,
        typer.Option("--dedup-report", help="dedup_report.json, so clusters stay split-safe."),
    ] = None,
    val_bytes: Annotated[
        int,
        typer.Option("--val-bytes", help="Diagnostic-validation byte target."),
    ] = 50 * 1024 * 1024,
    quick_bytes: Annotated[
        int,
        typer.Option(
            "--quick-bytes", help="Quick-validation byte target, nested inside validation."
        ),
    ] = 5 * 1024 * 1024,
    audit_bytes: Annotated[
        int,
        typer.Option("--audit-bytes", help="Text-audit byte target."),
    ] = 5 * 1024 * 1024,
    seed: Annotated[int, typer.Option("--seed", help="Frozen split assignment seed.")] = 20260919,
) -> None:
    """Assign frozen, group-safe train / diagnostic / audit splits (C05)."""
    if not input_path.exists():
        typer.echo(f"Error: input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        documents = list(_read_canonical_input(input_path))
    except (FileNotFoundError, NotADirectoryError, ValueError, TypeError) as e:
        typer.echo(f"Error loading input documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    result: DedupResult | None = None
    if dedup_report is not None:
        if not dedup_report.is_file():
            typer.echo(f"Error: dedup report '{dedup_report}' not found.", err=True)
            raise typer.Exit(code=1)
        result = _load_dedup_report(dedup_report)

    config = SplitConfig(
        diagnostic_val_target_bytes=val_bytes,
        quick_val_target_bytes=quick_bytes,
        audit_target_bytes=audit_bytes,
        seed=seed,
    )
    assignment = assign_splits(documents, result, config)

    leaks = verify_group_disjointness(
        assignment, {g.group_id: g.doc_ids for g in build_split_groups(documents, result)}
    )
    if leaks:
        typer.echo(
            f"Error: {len(leaks)} split group(s) straddle a split boundary: {leaks[:5]}",
            err=True,
        )
        raise typer.Exit(code=1)

    writer = CanonicalDatasetWriter(output_dir)
    writer.write_jsonl(apply_splits(documents, assignment), filename="documents.jsonl")

    frozen = None
    if result is not None:
        frozen = freeze_pool(assignment, result)
        frozen.save(output_dir / "pool_freeze.json")

    (output_dir / "split_assignment.json").write_text(
        json.dumps(assignment.to_dict(), indent=2), encoding="utf-8"
    )

    typer.echo("============================================================")
    typer.echo(f"Split policy:    {assignment.policy_identity}")
    typer.echo(f"Groups:          {assignment.group_count:,} (assigned whole)")
    for split in ("train", "diagnostic_val", "audit"):
        typer.echo(
            f"  {split:<15} {assignment.achieved_documents[split]:>8,} docs  "
            f"{assignment.achieved_bytes[split]:>12,} bytes"
        )
    typer.echo(
        f"  quick subset    {len(assignment.quick_val_doc_ids):>8,} docs  "
        f"{assignment.quick_val_bytes:>12,} bytes (nested in diagnostic_val)"
    )
    if frozen is not None:
        typer.echo(f"Pool frozen:     {frozen.pool_id}")
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


def _read_canonical_input(input_path: Path) -> Iterator[CanonicalDocument]:
    """Stream canonical documents from a file or a shard directory."""
    if input_path.is_dir():
        return CanonicalDatasetReader.read_shards(input_path)
    if input_path.suffix == ".parquet":
        return CanonicalDatasetReader.read_parquet(input_path)
    return CanonicalDatasetReader.read_jsonl(input_path)


def _load_dedup_report(path: Path) -> DedupResult:
    """Rehydrate a DedupResult from a saved dedup report."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return DedupResult(
        config_identity=data["config_identity"],
        clusters=[DuplicateCluster(**c) for c in data["clusters"]],
        lineage_groups=data["lineage_groups"],
        stats=DedupStats(**data["stats"]),
        survivor_doc_ids=data["survivor_doc_ids"],
        dropped_doc_ids=data["dropped_doc_ids"],
    )


pool_app = typer.Typer(name="pool", help="Assemble, inspect and verify frozen clean-text pools.")
app.add_typer(pool_app, name="pool")


def _load_views(path: Path) -> list[SourceView]:
    """Load source view definitions from a YAML or JSON file."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    if not isinstance(data, dict) or "views" not in data:
        raise ValueError("view file must be a mapping with a top-level 'views' list")
    return [SourceView(**entry) for entry in data["views"]]


def _load_binding(path: Path) -> PoolBinding:
    """Load the pool binding (revisions, policies, licenses) from YAML or JSON."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("binding file must be a mapping")
    return PoolBinding(**data)


@pool_app.command("build")
def pool_build_cmd(
    input_path: Annotated[
        Path, typer.Option("--input", "-i", help="Split canonical dataset file or directory.")
    ],
    views_path: Annotated[
        Path, typer.Option("--views", help="YAML/JSON file defining source views.")
    ],
    binding_path: Annotated[
        Path, typer.Option("--binding", help="YAML/JSON file defining the pool binding.")
    ],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for the pool and its manifest.")
    ],
    budget_tokens: Annotated[
        int,
        typer.Option("--budget-tokens", help="Planned token budget, for sufficiency accounting."),
    ] = 0,
    split_assignment_path: Annotated[
        Path | None,
        typer.Option(
            "--split-assignment",
            help="split_assignment.json, so the fixed quick-validation subset is carried forward.",
        ),
    ] = None,
    multi_view: Annotated[
        bool,
        typer.Option(
            "--multi-view/--exclusive",
            help="Overlap treatment: keep a document in every matching view, or assign it once.",
        ),
    ] = False,
) -> None:
    """Assemble an immutable clean-text pool from deterministic source views (C05)."""
    for label, path in (("input", input_path), ("views", views_path), ("binding", binding_path)):
        if not path.exists():
            typer.echo(f"Error: {label} path '{path}' does not exist.", err=True)
            raise typer.Exit(code=1)

    try:
        views = _load_views(views_path)
        binding = _load_binding(binding_path)
        documents = list(_read_canonical_input(input_path))
    except (ValueError, TypeError, KeyError) as e:
        typer.echo(f"Error loading pool inputs: {e}", err=True)
        raise typer.Exit(code=1) from e

    policy = OverlapPolicy.MULTI_VIEW if multi_view else OverlapPolicy.EXCLUSIVE_ASSIGNMENT
    config = PoolBuildConfig(planned_budget_tokens=budget_tokens, overlap_policy=policy)

    quick_ids: list[str] = []
    if split_assignment_path is not None:
        if not split_assignment_path.is_file():
            typer.echo(f"Error: split assignment '{split_assignment_path}' not found.", err=True)
            raise typer.Exit(code=1)
        quick_ids = json.loads(split_assignment_path.read_text(encoding="utf-8")).get(
            "quick_val_doc_ids", []
        )

    try:
        assembly = build_pool(documents, views, binding, config, quick_val_doc_ids=quick_ids)
    except (PoolPublicationError, LeakDetectedError, ValueError) as e:
        typer.echo(f"Error: pool publication refused: {e}", err=True)
        raise typer.Exit(code=1) from e

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = assembly.manifest
    CanonicalDatasetWriter(output_dir).write_jsonl(
        assembly.accepted_documents, filename="documents.jsonl"
    )
    manifest.save(output_dir / "pool_manifest.json")

    _print_pool_summary(manifest)
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


@pool_app.command("inspect")
def pool_inspect_cmd(
    manifest_path: Annotated[
        Path, typer.Option("--manifest", "-m", help="pool_manifest.json to inspect.")
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Emit the manifest as JSON.")] = False,
) -> None:
    """Inspect a frozen pool manifest."""
    if not manifest_path.is_file():
        typer.echo(f"Error: manifest '{manifest_path}' not found.", err=True)
        raise typer.Exit(code=1)

    manifest = FrozenPoolManifest.load(manifest_path)
    if as_json:
        typer.echo(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
        return

    _print_pool_summary(manifest)
    audit = manifest.overlap_audit
    typer.echo(
        f"Independence:    {audit.get('view_count', 0)} view(s) over "
        f"{audit.get('family_count', 0)} distinct source family/families"
    )
    typer.echo("============================================================")


@pool_app.command("verify")
def pool_verify_cmd(
    manifest_path: Annotated[
        Path, typer.Option("--manifest", "-m", help="pool_manifest.json to verify against.")
    ],
    input_path: Annotated[
        Path, typer.Option("--input", "-i", help="Pool documents file or directory.")
    ],
) -> None:
    """Verify a frozen pool offline against its stored documents."""
    if not manifest_path.is_file():
        typer.echo(f"Error: manifest '{manifest_path}' not found.", err=True)
        raise typer.Exit(code=1)
    if not input_path.exists():
        typer.echo(f"Error: input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)

    manifest = FrozenPoolManifest.load(manifest_path)
    try:
        documents = list(_read_canonical_input(input_path))
    except (ValueError, TypeError, FileNotFoundError, NotADirectoryError) as e:
        typer.echo(f"Error loading pool documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    problems = verify_pool_offline(manifest, documents)
    if problems:
        typer.echo(f"Verification FAILED for pool '{manifest.pool_id}':", err=True)
        for problem in problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(code=1)

    typer.echo(
        f"Verification SUCCESS: pool '{manifest.pool_id}' ({manifest.tier}) matches its "
        f"manifest across {manifest.document_count:,} documents "
        "(membership and content digests both re-derived offline)."
    )


@app.command("freeze")
def freeze_cmd(
    pool_manifest_path: Annotated[
        Path, typer.Option("--pool-manifest", help="pool_manifest.json for the frozen pool.")
    ],
    input_path: Annotated[
        Path, typer.Option("--input", "-i", help="Pool documents file or directory.")
    ],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for the regime and fit manifests.")
    ],
    vocab_size: Annotated[
        int, typer.Option("--vocab-size", help="Target BPE vocabulary size.")
    ] = 32768,
    fit_sample_bytes: Annotated[
        int,
        typer.Option("--fit-sample-bytes", help="Canonical bytes to draw for the tokenizer fit."),
    ] = 512 * 1024 * 1024,
    fit_tokenizer: Annotated[
        bool,
        typer.Option(
            "--fit/--plan-only",
            help="Actually fit the tokenizer, or only emit the resource plan.",
        ),
    ] = False,
) -> None:
    """Freeze the tokenizer-fit sample, tokenizer and research regime (C06)."""
    if not pool_manifest_path.is_file():
        typer.echo(f"Error: pool manifest '{pool_manifest_path}' not found.", err=True)
        raise typer.Exit(code=1)

    manifest = FrozenPoolManifest.load(pool_manifest_path)
    try:
        documents = list(_read_canonical_input(input_path))
    except (ValueError, TypeError, FileNotFoundError, NotADirectoryError) as e:
        typer.echo(f"Error loading pool documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    output_dir.mkdir(parents=True, exist_ok=True)

    membership = manifest.view_membership.get("doc_ids_by_view", {})
    train_membership = restrict_membership_to_split(membership, documents, "train")
    declared = dict(manifest.view_membership.get("declared_shares", {}))

    fit_manifest = build_tokenizer_fit_manifest(
        documents,
        train_membership,
        declared,
        manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=fit_sample_bytes),
    )
    (output_dir / "tokenizer_fit_manifest.json").write_text(
        json.dumps(fit_manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )

    plan = tokenizer_fit_resource_plan(fit_manifest, vocab_size)
    (output_dir / "tokenizer_fit_resource_plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8"
    )

    typer.echo("============================================================")
    typer.echo(f"Pool:            {manifest.pool_id} ({manifest.tier})")
    typer.echo(
        f"Fit sample:      {fit_manifest.document_count:,} docs, {fit_manifest.total_bytes:,} bytes"
    )
    typer.echo(f"Fit identity:    {fit_manifest.fit_id}")
    typer.echo(
        f"Resource plan:   ~{plan['estimated_peak_memory_gib']} GiB peak (estimate), "
        f"authorization required: {plan['authorization_required']}"
    )

    if not fit_tokenizer:
        typer.echo("Tokenizer fit:   PLAN ONLY (no fit executed; pass --fit to run it).")
        typer.echo(f"Output:          {output_dir}")
        typer.echo("============================================================")
        return

    if plan["authorization_required"]:
        typer.echo(
            "Error: this fit exceeds the unattended sample budget and needs an explicit "
            "operator resource authorization. Re-run with a smaller --fit-sample-bytes "
            "or obtain authorization.",
            err=True,
        )
        raise typer.Exit(code=1)

    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    fit_docs = [d for d in documents if d.doc_id in set(fit_manifest.doc_ids)]
    try:
        tokenizer = ByteLevelBPETokenizer.train_from_documents(
            fit_docs,
            target_vocab_size=vocab_size,
            is_production_baseline=manifest.is_production_ready,
        )
    except ValueError as e:
        typer.echo(f"Error: tokenizer fit refused: {e}", err=True)
        raise typer.Exit(code=1) from e

    tokenizer_dir = output_dir / "tokenizer"
    tokenizer.save(tokenizer_dir)

    diagnostic_ids = sorted(d for d, s in manifest.split_of_doc.items() if s == "diagnostic_val")
    quick_ids = sorted(manifest.inventory.quick_val_doc_ids)
    byte_of = {d.doc_id: d.utf8_byte_count for d in documents}
    diagnostic = DiagnosticCorpusFreeze(
        diagnostic_doc_ids=diagnostic_ids,
        quick_doc_ids=[q for q in quick_ids if q in set(diagnostic_ids)],
        diagnostic_bytes=sum(byte_of.get(d, 0) for d in diagnostic_ids),
        quick_bytes=sum(byte_of.get(d, 0) for d in quick_ids if d in set(diagnostic_ids)),
    )

    regime = build_regime(
        pool_id=manifest.pool_id,
        pool_content_digest=manifest.content_digest,
        tokenizer_fingerprint=tokenizer.fingerprint,
        tokenizer_fit_id=fit_manifest.fit_id,
        diagnostic=diagnostic,
        exclusion_policy_identity=manifest.binding.exclusion_policy_identity or "none_declared",
        split_policy_identity=manifest.binding.split_policy_identity,
        pool_tier=manifest.tier,
        notes=[
            "Research regime: binds pool, tokenizer and diagnostic corpus. It does NOT "
            "bind a mixture; a campaign freeze does that after data search.",
        ],
    )
    regime.save(output_dir / "research_regime.json")

    typer.echo(
        f"Tokenizer:       {tokenizer.actual_vocab_size:,} tokens, fp {tokenizer.fingerprint[:16]}"
    )
    typer.echo(f"Production base: {tokenizer.is_production_baseline}")
    typer.echo(f"Regime:          {regime.regime_id} (no mixture bound)")
    typer.echo(
        f"Diagnostic:      {len(diagnostic.diagnostic_doc_ids):,} docs, "
        f"quick {len(diagnostic.quick_doc_ids):,}"
    )
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


def _print_pool_summary(manifest: FrozenPoolManifest) -> None:
    """Print the shared pool summary block."""
    typer.echo("============================================================")
    typer.echo(f"Pool:            {manifest.pool_id}")
    typer.echo(f"Tier:            {manifest.tier.upper()}")
    typer.echo(f"Documents:       {manifest.document_count:,}")
    for split in sorted(manifest.inventory.documents):
        typer.echo(
            f"  {split:<15} {manifest.inventory.documents[split]:>8,} docs  "
            f"{manifest.inventory.bytes.get(split, 0):>12,} bytes"
        )
    sufficiency = manifest.sufficiency
    if sufficiency:
        typer.echo(
            f"Sufficiency:     ~{sufficiency.get('estimated_unique_tokens', 0):,} unique tokens "
            f"(estimate); sufficient without repetition: "
            f"{sufficiency.get('is_sufficient_without_repetition')}"
        )
    for note in manifest.notes:
        typer.echo(f"  NOTE: {note}")


@app.command("mix01-status")
def mix01_status_cmd(
    catalog_path: Annotated[
        Path,
        typer.Option("--catalog", "-c", help="Path to dataset catalog YAML."),
    ] = Path("manifests/datasets.catalog.yaml"),
    views_path: Annotated[
        Path, typer.Option("--views", "-v", help="Mix01 view registry YAML.")
    ] = Path("recipes/mixtures/mix01_views.yaml"),
    preset_path: Annotated[
        Path | None,
        typer.Option("--preset", "-p", help="Gate one preset for a run; exit 1 when blocked."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the status as JSON.")] = False,
) -> None:
    """Report READY / BLOCKED / NOT LIVE-VERIFIED for every mix01 view (C04, P13).

    Admission evidence comes from the catalog and the P01 artifact store; nothing
    is presumed admitted. With --preset, the named treatment is gated for a run
    and the command fails loudly when any component blocks.
    """
    from xlm.data.sources.mix01 import (
        Mix01BlockedError,
        gate_preset_for_run,
        load_mix01_views,
        load_mixture_preset,
        mix01_status,
    )

    try:
        catalog = load_catalog(catalog_path)
        registry = load_mix01_views(views_path)
    except Exception as e:
        typer.echo(f"Error loading mix01 inputs: {e}", err=True)
        raise typer.Exit(code=1) from e

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    auditor = CatalogAuditor(catalog=catalog, artifact_store=store)
    admission = {
        candidate.source_id: auditor.audit_source(candidate).status.value
        for candidate in catalog.sources
    }

    if preset_path is not None:
        try:
            preset = load_mixture_preset(preset_path)
            gated = gate_preset_for_run(preset, registry, admission)
        except (Mix01BlockedError, ValueError) as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"Preset '{preset.id}' is gated READY ({len(gated)} components).")
        return

    report = mix01_status(registry, admission)
    if as_json:
        typer.echo(json.dumps([s.to_dict() for s in report], indent=2))
        return

    typer.echo("============================================================")
    typer.echo("Mix01 view readiness (nothing is presumed admitted)")
    for status in report:
        typer.echo(f"  [{status.readiness.value.upper():<17}] {status.component_id:<36}")
        for reason in status.reasons:
            typer.echo(f"      - {reason}")
    ready = sum(1 for s in report if s.readiness.value == "ready")
    typer.echo(f"Ready: {ready}/{len(report)}; live data pilot: NOT RUN")
    typer.echo("============================================================")


@app.command("tokenize")
def tokenize_cmd(
    input_path: Annotated[
        Path, typer.Option("--input", "-i", help="Pool documents file or directory.")
    ],
    tokenizer_dir: Annotated[
        Path, typer.Option("--tokenizer", "-t", help="Frozen tokenizer directory.")
    ],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Root directory for per-source shards.")
    ],
    pool_id: Annotated[
        str, typer.Option("--pool-id", help="Pool identity the shards are bound to.")
    ] = "unbound_pool",
    split: Annotated[
        str, typer.Option("--split", help="Which split to tokenize into shards.")
    ] = "train",
) -> None:
    """Build bounded per-source token shards from a frozen pool (C07).

    Shards are per source and independent of any mixture, so changing mixture ratios
    never forces retokenization.
    """
    if not input_path.exists():
        typer.echo(f"Error: input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)
    if not (tokenizer_dir / "tokenizer.json").is_file():
        typer.echo(f"Error: no tokenizer found in '{tokenizer_dir}'.", err=True)
        raise typer.Exit(code=1)

    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    tokenizer = ByteLevelBPETokenizer.load(tokenizer_dir)

    try:
        documents = [d for d in _read_canonical_input(input_path) if d.split == split]
    except (ValueError, TypeError, FileNotFoundError, NotADirectoryError) as e:
        typer.echo(f"Error loading pool documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    if not documents:
        typer.echo(f"Error: no documents in split '{split}'.", err=True)
        raise typer.Exit(code=1)

    by_source: dict[str, list[CanonicalDocument]] = {}
    for document in documents:
        by_source.setdefault(document.source_id, []).append(document)

    output_dir.mkdir(parents=True, exist_ok=True)
    typer.echo("============================================================")
    typer.echo(
        f"Tokenizer:       {tokenizer.fingerprint[:16]} ({tokenizer.actual_vocab_size:,} tokens)"
    )

    total_targets = 0
    for source_id in sorted(by_source):
        shard_dir = output_dir / source_id
        writer = TokenShardWriter(
            shard_dir,
            shard_id=f"shard_{source_id}",
            source_id=source_id,
            tokenizer=tokenizer,
            pool_hash=pool_id,
        )
        manifest = writer.write_documents(by_source[source_id], add_special_tokens=True)
        counters = json.loads((shard_dir / "shard_counters.json").read_text(encoding="utf-8"))
        total_targets += counters["valid_targets"]
        typer.echo(
            f"  {source_id:<20} {manifest.num_documents:>6,} docs  "
            f"{manifest.num_tokens:>12,} tokens  "
            f"{counters['valid_targets']:>12,} valid targets  "
            f"dtype {manifest.token_dtype}  coverage {manifest.byte_coverage_ratio:.4f}"
        )

    typer.echo(f"Sources:         {len(by_source)}")
    typer.echo(f"Valid targets:   {total_targets:,}")
    typer.echo("Shards are per-source and mixture-independent; changing a mixture ratio")
    typer.echo("reuses these shards and does not require retokenization.")
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")
