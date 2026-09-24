"""Exclusive payload publication with durable, idempotent completion recovery.

Callers hold the plan execution lock. The journal FileLock protects intent and
settlement, including against independently constructed accounting managers.
An existing destination is accepted only when its file identity and SHA match
the private file named by a durable intent; equal bytes alone are not ownership.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import TYPE_CHECKING

from xlm.artifacts.manifest import canonical_payload_path, ensure_plain_path
from xlm.data.acquisition.disk import AtomicFileWriter
from xlm.data.acquisition.progress import (
    AcquisitionState,
    FileProgress,
    ProgressCorruptionError,
    PublicationIntent,
)

if TYPE_CHECKING:
    from xlm.data.acquisition.fetcher import BoundedFetcher


def _verify(path: Path, intent: PublicationIntent) -> None:
    ensure_plain_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size) != (
        intent.device,
        intent.inode,
        intent.size,
    ):
        raise ProgressCorruptionError("publication file identity/size mismatch")
    if AtomicFileWriter.hash_durable_prefix(path, intent.size) != intent.content_sha256:
        raise ProgressCorruptionError("publication content checksum mismatch")


def complete_publication(fetcher: BoundedFetcher, name: str) -> None:
    """Publish an admitted partial or reconcile its link; settle+complete atomically."""
    canonical_payload_path(name)
    with fetcher.journal.transaction() as state:
        fp = state.file_progress[name]
        intent = fp.publication
        if intent is None:
            raise ProgressCorruptionError("publication intent is missing")
        canonical_payload_path(intent.partial_path)
        allowed = (
            name in fetcher.plan.selected_files
            if fetcher.plan.mode.value == "whole_file"
            else name == "selected_records.jsonl"
        )
        if not allowed or intent.plan_hash != state.plan_hash or intent.destination != name:
            raise ProgressCorruptionError("publication intent identity mismatch")
        partial = fetcher.partial_dir / intent.partial_path
        destination = fetcher.output_dir / name
        ensure_plain_path(partial)
        ensure_plain_path(destination)
        pending = state.accounting.reservations.get("output", {})
        if fp.status == "completed" or pending.get(intent.output_token) != intent.size:
            raise ProgressCorruptionError("ambiguous publication settlement")
        if partial.exists():
            _verify(partial, intent)
        if destination.exists():
            _verify(destination, intent)
            if partial.exists():
                partial.unlink()  # verified private name only; link is already durable
        else:
            if not partial.exists():
                raise ProgressCorruptionError("publication payload is missing")
            AtomicFileWriter.atomic_complete(partial, destination)
            _verify(destination, intent)
        # The replacement journal is the single commit point for both fields.
        # If it fails, the old durable intent and reservation still authorize
        # recovery. Never infer whether an absent reservation was settled.
        state.accounting.occupancy["output"] = (
            state.accounting.occupancy.get("output", 0) + intent.size
        )
        del pending[intent.output_token]
        fp.bytes_downloaded = fp.verified_prefix_bytes = intent.size
        fp.content_sha256 = fp.prefix_sha256 = intent.content_sha256
        fp.etag = intent.etag or fp.etag
        fp.record_count, fp.status = intent.records, "completed"
        fp.publication = None


def publish_output(
    fetcher: BoundedFetcher,
    name: str,
    partial: Path,
    size: int,
    digest: str,
    records: int | None,
    etag: str | None = None,
) -> None:
    canonical_payload_path(name)
    relative = partial.relative_to(fetcher.partial_dir).as_posix()
    canonical_payload_path(relative)
    ensure_plain_path(partial)
    destination = fetcher.output_dir / name
    ensure_plain_path(destination)
    if destination.exists():
        raise ProgressCorruptionError("unowned publication destination exists")
    info = partial.stat()

    def admitted(state: AcquisitionState, token: str) -> None:
        fp = state.file_progress.setdefault(name, FileProgress(file_path=name))
        if fp.publication is not None or fp.status == "completed":
            raise ProgressCorruptionError("publication already admitted")
        fp.publication = PublicationIntent(
            plan_hash=state.plan_hash,
            partial_path=relative,
            destination=name,
            content_sha256=digest,
            size=size,
            device=info.st_dev,
            inode=info.st_ino,
            output_token=token,
            records=records,
            etag=etag,
        )

    # Reservation and intent share one durable transaction: neither can be
    # visible alone after death. The token is also this publication's attempt id.
    fetcher.capacity_mgr.reserve_disk_space(
        fetcher.output_dir,
        size,
        is_temp=False,
        publication=True,
        on_reserved=admitted,
    )
    try:
        complete_publication(fetcher, name)
    except OSError as exc:
        # Retrying the network body here could duplicate transfer after the
        # link succeeded. A fresh invocation reconciles the durable intent.
        raise ProgressCorruptionError("publication interrupted; restart to reconcile") from exc


def reconcile_publications(fetcher: BoundedFetcher) -> None:
    fetcher.journal.save()
    names = [
        name
        for name, fp in fetcher.journal.state.file_progress.items()
        if fp.publication is not None
    ]
    for name in names:
        complete_publication(fetcher, name)
