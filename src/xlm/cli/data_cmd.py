"""CLI commands for data import and processing adhering to C02, C03, and C04."""

from __future__ import annotations

import itertools
import json
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
    StageStats,
    create_pipeline_preset,
)
from xlm.data.dedup import (
    DedupConfig,
    DeduplicationEngine,
    DedupResult,
    DedupStats,
    DuplicateCluster,
    MinHashConfig,
    iter_surviving_documents,
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
    DiscoveryTransport,
    HttpsManifestTransport,
    HuggingFaceTransport,
    LocalManifestTransport,
    TransportBudget,
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
    sampling = SamplingFrame(
        selected_files=selected_files,
        selection_seed=seed,
        coverage_notes=coverage,
    )

    plan_id = f"plan_{source_id}_{view_id}_{cand.provider}"
    output_artifact_id = f"raw_{source_id}_{view_id}"

    # Build initial plan to compute its behavioral hash
    is_pilot = not (
        limits.max_transferred_bytes > 256 * 1024 * 1024
        or limits.max_records > 25_000
        or limits.max_output_disk_bytes > 2 * 1024 * 1024 * 1024
    )
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
        is_pilot=is_pilot,
        row_ranges=bounded_json(row_ranges_path),
        expected_file_digests=bounded_json(expected_digests_path) or {},
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
    typer.echo(f"Transferred:   <= {resolved_plan.limits.max_transferred_bytes:,} bytes")
    typer.echo(f"Output Disk:   <= {resolved_plan.limits.max_output_disk_bytes:,} bytes")
    typer.echo(f"Behavior Hash: {resolved_plan.plan_hash}")
    is_appr = bool(auth and auth.is_pilot_approved)
    typer.echo(f"Pilot Scope:   {resolved_plan.is_pilot} (Approved: {is_appr})")
    typer.echo(f"Saved to:      {out_file}")
    typer.echo("============================================================")


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
) -> None:
    """Execute composable data cleaning and filtering pipeline adhering to C01, C02, and C03."""
    if not input_path.exists():
        typer.echo(f"Error: Input path '{input_path}' does not exist.", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    q_dir = quarantine_dir or (output_dir / "quarantine")
    quarantine_mgr = QuarantineManager(q_dir)

    try:
        pipeline = create_pipeline_preset(preset)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    # Determine input loader. Directories stream every shard in stable sorted order;
    # reading only the first shard would silently drop the rest of the corpus.
    docs: Iterator[CanonicalDocument]
    try:
        if input_path.is_file():
            if input_path.suffix == ".parquet":
                docs = CanonicalDatasetReader.read_parquet(input_path)
            else:
                # Canonical JSONL when the first record parses as a canonical record,
                # otherwise fall back to the raw JSONL adapter.
                try:
                    docs_iter = CanonicalDatasetReader.read_jsonl(input_path)
                    first = next(docs_iter, None)
                    if first is not None:
                        docs = itertools.chain([first], docs_iter)
                    else:
                        docs = iter([])
                except (ValueError, TypeError):
                    adapter = JsonlAdapter(source_id="raw_input")
                    docs = adapter.process_file(input_path)
        else:
            docs = CanonicalDatasetReader.read_shards(input_path)
    except (FileNotFoundError, NotADirectoryError, ValueError, TypeError) as e:
        typer.echo(f"Error loading input documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Starting cleaning pipeline with preset '{preset}'...")
    accepted_iter, summary = pipeline.run_stream(
        documents=docs,
        quarantine_mgr=quarantine_mgr,
        max_docs=max_docs,
    )

    # Stream accepted records straight to disk; never hold the accepted corpus in RAM.
    writer = CanonicalDatasetWriter(output_dir)
    jsonl_path = writer.write_jsonl(accepted_iter, filename="documents.jsonl")

    # Parquet is produced by re-reading the JSONL shard, which keeps the second
    # write bounded as well. A Parquet failure is reported, never silently ignored.
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
) -> None:
    """Deduplicate canonical documents across source families (C05)."""
    if not input_path.exists():
        typer.echo(f"Error: input path '{input_path}' does not exist.", err=True)
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

    try:
        documents = list(_read_canonical_input(input_path))
    except (FileNotFoundError, NotADirectoryError, ValueError, TypeError) as e:
        typer.echo(f"Error loading input documents: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Deduplicating {len(documents):,} documents (identity {config.identity()})...")
    result = DeduplicationEngine(config).run(documents, scratch)

    writer = CanonicalDatasetWriter(output_dir)
    survivors = list(iter_surviving_documents(documents, result))
    writer.write_jsonl(survivors, filename="documents.jsonl")
    (output_dir / "dedup_report.json").write_text(
        json.dumps(result.to_dict(), indent=2), encoding="utf-8"
    )

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
    typer.echo(f"Output:          {output_dir}")
    typer.echo("============================================================")


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
