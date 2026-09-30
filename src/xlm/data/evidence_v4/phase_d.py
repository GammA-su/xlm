"""The v4.1 Phase-D acquirer: THE frozen Phase-D plan on the reviewed v4 engine.

Public entry points:

- :func:`run_live` — the only live path: THE committed Phase-D plan (confirmed
  by its exact digest), THE fresh Phase-D root, THE read-only COMPLETE v4.1
  Phase-P parent root and the live HTTPS transport with the exact v4.1 host
  set;
- :func:`run_offline` — authored synthetic fixtures only (synthetic plan,
  synthetic parent, scripted transport, never a frozen root);
- :func:`inspect` — read-only status of a root;
- :func:`verify_repository` — offline recomputation of every committed binding.

Acquisition is the unchanged v4 engine (attempt row before request, B01
partial-byte accounting, B02 absolute deadline, exact redirects/identity,
retries, restart reconciliation); only the per-arm caps, the root cap and the
free-space floor are Phase D's. Decoding and exports follow acquisition. None
of the entry points accepts a URL, file, range, ETag, host, root, locator or
output path (protocol sections 3-10).

The root cap covers every file under the root through the final export
(protocol section 6), and the bound Phase-P parent is re-verified before
decoding and again at completion (section 1): a run becomes COMPLETE only
through :meth:`_PhaseDEngine._seal`.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, phase_p, state, v41, verify
from xlm.data.evidence_v4 import phase_d_decode as dec
from xlm.data.evidence_v4 import phase_d_plan as pd
from xlm.data.evidence_v4 import transport as tp

RECEIPT = "phase_d_receipt.json"
MANIFEST = "artifact_manifest.json"
REQUESTS = "request_receipt.jsonl"
M_OUTPUT = "m_selected_metadata.jsonl"
M_MANIFEST = "m_acquisition_manifest.json"
T_DOCUMENTS = "sealed/t_selected_documents.jsonl"
T_PROVENANCE = "sealed/t_provenance.json"
T_MANIFEST = "t_acquisition_manifest.json"
ARM_OUTPUTS: dict[str, tuple[str, ...]] = {
    "M": (M_OUTPUT, M_MANIFEST),
    "T": (T_DOCUMENTS, T_PROVENANCE, T_MANIFEST),
}
EXPORTS = (REQUESTS, RECEIPT, MANIFEST, *(n for names in ARM_OUTPUTS.values() for n in names))
SUPERSEDED = ".superseded"
# SQLite growth (database pages plus the rollback journal) of one single-row
# transaction is not known before it commits: this much root-cap room is
# required before each one, and the whole root is measured again after it.
STORE_WRITE_RESERVE_BYTES = 65536
FROZEN_ROOTS = (*phase_p.FROZEN_ROOTS, pd.EXECUTION_ROOT)

RefusedError = phase_p.RefusedError
StopError = phase_p.StopError


def _free_bytes(path: Path) -> int:
    return phase_p._free_bytes(path)


def _root_bytes(root: Path) -> int:
    return phase_p._root_bytes(root)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _binding(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": _sha(raw)}


class PhaseDStore(state.Store):
    """The v4 receipt store plus one table of decoded selected outputs."""

    op_id_pattern = pd.OP_ID

    def __init__(self, path: Path, *, create: bool) -> None:
        super().__init__(path, create=create)
        try:
            if create:
                with self._tx() as db:
                    db.execute(
                        "CREATE TABLE decoded_outputs (name TEXT PRIMARY KEY, arm TEXT NOT NULL, "
                        "records INTEGER NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, "
                        "updated_utc TEXT NOT NULL)"
                    )
            self.decoded()
        except Exception as exc:
            self.close()
            raise state.StateError(f"not a Phase-D state store: {exc}") from exc

    def decoded(self) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM decoded_outputs ORDER BY name")
        return {r["name"]: dict(r) for r in rows}

    def record_decoded(self, name: str, arm: str, records: int, raw: bytes, now: str) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO decoded_outputs VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (name) DO "
                "UPDATE SET arm = excluded.arm, records = excluded.records, bytes = "
                "excluded.bytes, sha256 = excluded.sha256, updated_utc = excluded.updated_utc",
                (name, arm, records, len(raw), _sha(raw), now),
            )


@dataclass(frozen=True)
class ArmOutcome:
    arm: str
    status: str  # COMPLETE | INCOMPLETE
    reason: str | None
    files: dict[str, bytes] = field(default_factory=dict)
    records: dict[str, int] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


def _incomplete(arm: str, reason: str) -> ArmOutcome:
    return ArmOutcome(arm, "INCOMPLETE", reason)


@dataclass(frozen=True)
class Result:
    status: str  # COMPLETE | INCOMPLETE
    run_status: str
    stop_reason: str | None
    arms: dict[str, dict[str, Any]]
    totals: dict[str, Any]
    root: str


class _ParentError(ValueError):
    pass


_PARENT_ERRORS = (_ParentError, canonical.CanonicalError, OSError, KeyError, TypeError, IndexError)


class _PhaseDEngine(phase_p._Engine):
    store_class = PhaseDStore

    def __init__(
        self,
        dplan: pd.PhaseDPlan,
        root: Path,
        parent_root: Path,
        transport: tp.Transport,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        super().__init__(dplan.fetch, root, transport, sleep=sleep, clock=clock)
        self.dplan = dplan
        self.parent_root = parent_root
        self._arms: dict[str, ArmOutcome] = {}

    @property
    def dstore(self) -> PhaseDStore:
        store = self.store
        if not isinstance(store, PhaseDStore):
            raise state.StateError("the open store is not a Phase-D store")
        return store

    # -- top level ---------------------------------------------------------

    def run(self) -> Result:  # type: ignore[override]
        if _free_bytes(self.root) < pd.FREE_DISK_BYTES_MIN:
            raise RefusedError("less than 2 GiB free on the execution root's drive")
        self._verify_parents()
        with self._open_store() as store:
            self._store = store
            run = store.run()
            if (
                run.protocol_version,
                run.plan_digest,
                run.selection_digest,
                run.source_revision,
                run.synthetic,
            ) != (
                pd.PROTOCOL_VERSION,
                self.dplan.digest,
                frozen.SELECTION_DIGEST,
                self.plan.source.revision,
                self.plan.synthetic,
            ):
                raise RefusedError("the root's run row binds a different protocol/plan/source")
            if run.status == "STOPPED":
                raise RefusedError(f"root is STOPPED ({run.stop_reason}); review required")
            try:
                self._reconcile()
                if run.status == "COMPLETE":
                    self._verify_exports()
                    return self._phase_d_result()
                stop = self._acquire()
                arms = self._decode_arms()
            except StopError as exc:
                store.set_status("STOPPED", str(exc), phase_p._utc())
                self._arms = {a: _incomplete(a, f"run stopped: {exc}") for a in ("M", "T")}
                self._write_exports()
                return self._phase_d_result()
            reason = stop or next((a.reason for a in arms.values() if a.status != "COMPLETE"), None)
            if reason is not None:
                store.set_status("STOPPED", reason, phase_p._utc())
            self._write_exports()  # a still-RUNNING run is COMPLETE only through the seal
            return self._phase_d_result()

    def _acquire(self) -> str | None:
        """Execute every incomplete operation, M then T; a STOP ends all requests."""
        try:
            for op in self.plan.operations:
                if not self._is_complete(op):
                    self._execute(op)
                    self._root_room(0, f"after {op.op_id}")
        except StopError as exc:
            return str(exc)
        return None

    # -- whole-root cap --------------------------------------------------------

    def _root_room(self, growth: int, what: str) -> None:
        """STOP unless every file now under the root plus ``growth`` bytes fits the root cap."""
        used = _root_bytes(self.root)
        if used + growth > pd.ROOT_BYTES_MAX:
            raise StopError(
                f"{what}: execution root holds {used} bytes; {growth} more would exceed "
                f"the {pd.ROOT_BYTES_MAX}-byte root cap"
            )

    def _publish(self, name: str, raw: bytes) -> None:
        """Atomically publish one export, refused before any write that would pass the cap.

        The temp file coexists with a destination it replaces, so the peak is
        the measured root (old file included) plus the whole new file.
        """
        path = self.root / name
        if path.is_file() and path.read_bytes() == raw:
            return
        self._root_room(len(raw), f"publishing {name}")
        canonical.write_atomic(path, raw)
        self._root_room(0, f"published {name}")

    def _demote(self, names: tuple[str, ...]) -> list[str]:
        """Rename existing exports to ``.superseded`` (kept, no new bytes); returns those kept."""
        kept: list[str] = []
        for name in names:
            path = self.root / name
            if path.exists():
                os.replace(path, path.with_name(path.name + SUPERSEDED))
            if path.with_name(path.name + SUPERSEDED).exists():
                kept.append(name + SUPERSEDED)
        return kept

    def _precheck(self, arm: str) -> None:
        count, body = self.store.arm_totals(arm)
        attempts_max = pd.ATTEMPTS_PER_ARM_MAX[arm]
        body_max = pd.BODY_BYTES_PER_ARM_MAX[arm]
        if count >= attempts_max:
            raise StopError(f"arm {arm}: physical attempt cap {attempts_max} reached")
        if body + phase_p.RESPONSE_READ_LIMIT > body_max:
            raise StopError(
                f"arm {arm}: {body} body bytes leave no room for one more response "
                f"under the {body_max}-byte arm cap"
            )
        used = _root_bytes(self.root)
        if used + phase_p.RESPONSE_READ_LIMIT > pd.ROOT_BYTES_MAX:
            raise StopError(
                f"execution root holds {used} bytes; one more response could exceed "
                f"the {pd.ROOT_BYTES_MAX}-byte root cap"
            )

    # -- Phase-P parent ------------------------------------------------------

    def _read_parent(self, artifact: pd.ParentArtifact) -> bytes:
        path = self.parent_root / artifact.rel
        if not path.is_file():
            raise _ParentError(f"{artifact.rel} is missing")
        raw = path.read_bytes()
        if (len(raw), _sha(raw)) != (artifact.bytes, artifact.sha256):
            raise _ParentError(f"{artifact.rel} differs from its bound bytes/SHA-256")
        return raw

    def _parent_bytes(self, rel: str) -> bytes:
        try:
            return self._read_parent(self.dplan.parents.artifact(rel))
        except _ParentError as exc:
            raise StopError(f"Phase-P parent changed during Phase D: {exc}") from exc

    def _verify_parents(self) -> None:
        """Every bound Phase-P artifact must reproduce before any state change (read-only)."""
        try:
            self._check_parents()
        except _PARENT_ERRORS as exc:
            raise RefusedError(f"Phase-P parent differs from the frozen binding: {exc}") from exc

    def _recheck_parents(self, boundary: str) -> None:
        """The same verification later in the run: any drift is STOP, never COMPLETE."""
        try:
            self._check_parents()
        except _PARENT_ERRORS as exc:
            raise StopError(f"Phase-P parent changed during Phase D ({boundary}): {exc}") from exc

    def _check_parents(self) -> None:
        parents = self.dplan.parents
        docs: dict[str, Any] = {}
        for artifact in parents.artifacts:
            raw = self._read_parent(artifact)
            if artifact.digest is not None:
                doc = canonical.loads_bytes_strict(raw)
                if canonical.self_digest(doc) != doc.get("digest") or (
                    doc.get("digest") != artifact.digest
                ):
                    raise _ParentError(f"{artifact.rel} self-digest differs")
                docs[artifact.rel] = doc
        receipt = docs["phase_p_receipt.json"]
        manifest = docs["artifact_manifest.json"]
        for doc in docs.values():
            if doc.get("plan_digest") != parents.phase_p_plan_digest:
                raise _ParentError("a Phase-P export binds a different Phase-P plan")
        if (receipt["status"], receipt["run_status"], manifest["status"]) != (
            "COMPLETE",
            "COMPLETE",
            "COMPLETE",
        ) or manifest["receipt_digest"] != receipt["digest"]:
            raise _ParentError("the Phase-P parent is not a COMPLETE, manifest-bound run")
        for artifact in parents.artifacts:
            if artifact.rel.startswith("payload/") and manifest["artifacts"].get(artifact.rel) != {
                "bytes": artifact.bytes,
                "sha256": artifact.sha256,
            }:
                raise _ParentError(f"{artifact.rel} differs from the Phase-P manifest")
        self._check_layouts(docs["m_phase_p_layout.json"], docs["t_phase_p_layout.json"])

    def _check_layouts(self, m_layout: dict[str, Any], t_layout: dict[str, Any]) -> None:
        if len(m_layout["files"]) != len(self.dplan.m_files) or len(t_layout["files"]) != len(
            self.dplan.t_files
        ):
            raise _ParentError("Phase-P layouts cover a different file set")
        for f in self.dplan.m_files:
            entry = m_layout["files"][f.ordinal]
            lay = entry["layout"]
            chunks = tuple(
                pd.Chunk(c["path"], c["start"], c["end_exclusive"], c["compressed_bytes"])
                for c in lay["projected_chunks"]
            )
            if (
                (entry["file"], entry["remote_length"], entry["strong_etag"])
                != (f.file, f.remote_length, f.strong_etag)
                or tuple(lay["window"]) != f.window
                or (
                    lay["window_row_group"],
                    lay["window_row_group_first_row"],
                    lay["window_row_group_rows"],
                )
                != (f.row_group, f.row_group_first_row, f.row_group_rows)
                or chunks != f.chunks
                or lay["footer_range"][0] != f.footer_start
            ):
                raise _ParentError(f"M-{f.ordinal:02d} differs from the Phase-P M layout")
        for g in self.dplan.t_files:
            entry = t_layout["files"][g.ordinal]
            chunk = entry["layout"]["selected_text_chunk"]
            if (
                (entry["file"], entry["remote_length"], entry["strong_etag"])
                != (g.file, g.remote_length, g.strong_etag)
                or (chunk["start"], chunk["end_exclusive"]) != g.span
                or (chunk["row_group"], chunk["first_row"], chunk["num_rows"])
                != (g.row_group, g.row_group_first_row, g.row_group_rows)
                or chunk["uncompressed_bytes"] != g.uncompressed_bytes
                or entry["footer_range"][0] != g.footer_start
            ):
                raise _ParentError(f"T-{g.ordinal:02d} differs from the Phase-P T layout")

    # -- decoding ------------------------------------------------------------

    def _decode_arms(self) -> dict[str, ArmOutcome]:
        self._recheck_parents("before decoding")
        arms = {arm: self._decode(arm) for arm in ("M", "T")}
        new = sum(len(raw) for a in arms.values() for raw in a.files.values())
        if new and _root_bytes(self.root) + new > pd.ROOT_BYTES_MAX:
            reason = f"decoded outputs would exceed the {pd.ROOT_BYTES_MAX}-byte root cap"
            arms = {
                arm: (_incomplete(arm, reason) if a.status == "COMPLETE" else a)
                for arm, a in arms.items()
            }
        self._arms = arms
        return arms

    def _decode(self, arm: str) -> ArmOutcome:
        ops = [o for o in self.plan.operations if o.arm == arm]
        done = sum(1 for o in ops if self._is_complete(o))
        if done != len(ops):
            return _incomplete(arm, f"acquisition incomplete: {done}/{len(ops)} operations")
        try:
            return self._decode_m() if arm == "M" else self._decode_t()
        except dec.DecodeError as exc:
            return _incomplete(arm, f"decode: {exc}")

    def _payload_ref(self, op: frozen.Operation) -> dict[str, Any]:
        output = self.store.outputs()[op.op_id]
        return {
            "op_id": op.op_id,
            "range": list(op.range or ()),
            "retained_file": output["retained_file"],
            "bytes": output["bytes"],
            "sha256": output["sha256"],
        }

    def _decode_m(self) -> ArmOutcome:
        results: list[dec.MResult] = []
        for f in self.dplan.m_files:
            (op,) = self.dplan.ops_of("M", f.ordinal)
            payload = self._payload(op.op_id)
            footer = self._parent_bytes(f.footer_payload)
            results.append(dec.decode_m_file(f, payload, footer, self.plan.source))
        bundle = dec.assemble_m(self.dplan.m_files, results)
        files = []
        for f, r in zip(self.dplan.m_files, results, strict=True):
            (op,) = self.dplan.ops_of("M", f.ordinal)
            files.append(
                {
                    "ordinal": f.ordinal,
                    "file": f.file,
                    "window": list(f.window),
                    "row_group": f.row_group,
                    "row_group_first_row": f.row_group_first_row,
                    "row_group_rows": f.row_group_rows,
                    "projected_chunks": len(f.chunks),
                    "operation": self._payload_ref(op),
                    "phase_p_footer": f.footer_payload,
                    "records": len(r.lines),
                    "records_sha256": _sha(b"".join(r.lines)),
                    "decoded_row_group_rows": r.decoded_rows,
                    "decoder_reads": r.reads,
                }
            )
        records = sum(len(r.lines) for r in results)
        manifest = self._doc(
            "m_acquisition_manifest",
            status="COMPLETE",
            projection=list(frozen.PROJECTION),
            locator_field=dec.LOCATOR_FIELD,
            files=files,
            records=records,
            output={M_OUTPUT: _binding(bundle)},
            selector_decision="NONE (acquisition only)",
        )
        summary = {"records": records, "files": len(files)}
        return ArmOutcome(
            "M",
            "COMPLETE",
            None,
            files={M_OUTPUT: bundle, M_MANIFEST: canonical.canonical_bytes(manifest)},
            records={M_OUTPUT: records, M_MANIFEST: 1},
            summary=summary,
        )

    def _decode_t(self) -> ArmOutcome:
        results: list[dec.TResult] = []
        for g in self.dplan.t_files:
            chunk = b"".join(self._payload(o.op_id) for o in self.dplan.ops_of("T", g.ordinal))
            footer = self._parent_bytes(g.footer_payload)
            trailer = self._parent_bytes(g.trailer_payload)
            results.append(dec.decode_t_file(g, chunk, footer, trailer))
        bundle, retained, unique = dec.assemble_t(self.dplan.t_files, results)
        statuses: dict[str, int] = {s: 0 for s in dec.T_TERMINAL}
        provenance: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        for g, r in zip(self.dplan.t_files, results, strict=True):
            refs = [self._payload_ref(o) for o in self.dplan.ops_of("T", g.ordinal)]
            counts: dict[str, int] = {s: 0 for s in dec.T_TERMINAL}
            for d in r.documents:
                counts[d.status] += 1
                statuses[d.status] += 1
                provenance.append(
                    {
                        "locator": list(d.locator.identity),
                        "t_ordinal": g.ordinal,
                        "file": g.file,
                        "row_group": g.row_group,
                        "row_group_first_row": g.row_group_first_row,
                        "row_in_group": d.locator.row_in_group,
                        "chunk_span_half_open": list(g.span),
                        "operations": [ref["op_id"] for ref in refs],
                        "status": d.status,
                        "utf8_bytes": d.utf8_bytes,
                        "sha256": d.sha256,
                    }
                )
            files.append(
                {
                    "ordinal": g.ordinal,
                    "file": g.file,
                    "row_group": g.row_group,
                    "row_group_rows": g.row_group_rows,
                    "chunk_span_half_open": list(g.span),
                    "dictionary_page_offset": g.dictionary_page_offset,
                    "data_page_offset": g.data_page_offset,
                    "operations": refs,
                    "phase_p_footer": g.footer_payload,
                    "phase_p_trailer": g.trailer_payload,
                    "selected_locators": len(r.documents),
                    "status_counts": counts,
                    "decoded_rows": r.decoded_rows,
                    "unselected_rows_decoded_not_retained": r.decoded_rows - len(r.documents),
                    "decoder_reads": r.reads,
                }
            )
        locators = sum(len(r.documents) for r in results)
        sealed = self._doc(
            "t_sealed_provenance",
            access="SEALED custodian-only; never part of a reviewer package",
            locators=provenance,
        )
        sealed_raw = canonical.canonical_bytes(sealed)
        manifest = self._doc(
            "t_acquisition_manifest",
            status="COMPLETE",
            files=files,
            selected_locators=locators,
            status_counts=statuses,
            retained_text_bytes=retained,
            retained_unique_text_bytes=unique,
            limits={
                "document_utf8_bytes_max": pd.T_DOCUMENT_BYTES_MAX,
                "retained_text_bytes_max": pd.T_RETAINED_TEXT_BYTES_MAX,
            },
            sealed_outputs={T_DOCUMENTS: _binding(bundle), T_PROVENANCE: _binding(sealed_raw)},
            blinding=(
                "no selection category, custodian secret, review ID or reviewer order is "
                "generated or recorded by Phase D"
            ),
            unselected_rows=(
                "decoded only where mechanically required inside a selected chunk; never "
                "retained, exported, logged or rendered"
            ),
        )
        return ArmOutcome(
            "T",
            "COMPLETE",
            None,
            files={
                T_DOCUMENTS: bundle,
                T_PROVENANCE: sealed_raw,
                T_MANIFEST: canonical.canonical_bytes(manifest),
            },
            records={T_DOCUMENTS: locators, T_PROVENANCE: locators, T_MANIFEST: 1},
            summary={"selected_locators": locators, "status_counts": statuses},
        )

    # -- outputs -------------------------------------------------------------

    def _doc(self, kind: str, **payload: Any) -> dict[str, Any]:
        parents = self.dplan.parents
        body: dict[str, Any] = {
            "kind": f"essential_web_v41_phase_d_{kind}",
            "protocol_version": pd.PROTOCOL_VERSION,
            "protocol_sha256": pd.PROTOCOL_SHA256,
            "freeze_digest": pd.FREEZE_DIGEST,
            "plan_digest": self.dplan.digest,
            "dry_plan_digest": parents.dry_plan_digest,
            "phase_p_parent": {
                "root": parents.phase_p_root,
                "plan_digest": parents.phase_p_plan_digest,
                "parents_digest": parents.digest,
                "receipt_digest": parents.artifact("phase_p_receipt.json").digest,
                "manifest_digest": parents.artifact("artifact_manifest.json").digest,
                "m_layout_digest": parents.artifact("m_phase_p_layout.json").digest,
                "t_layout_digest": parents.artifact("t_phase_p_layout.json").digest,
            },
            "scientific_namespace": frozen.SCIENTIFIC_NAMESPACE,
            "selection_digest": frozen.SELECTION_DIGEST,
            "policy_digest": frozen.POLICY_DIGEST,
            "source": asdict(self.plan.source),
            "synthetic": self.plan.synthetic,
            **payload,
        }
        body["digest"] = canonical.self_digest(body)
        return body

    def _arm_entries(self) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        outputs = self.store.outputs()
        for arm in ("M", "T"):
            outcome = self._arms.get(arm, _incomplete(arm, "not decoded"))
            ops = [o for o in self.plan.operations if o.arm == arm]
            entries[arm] = {
                "status": outcome.status,
                "reason": outcome.reason,
                "logical_operations": len(ops),
                "logical_complete": sum(1 for o in ops if o.op_id in outputs),
                "summary": outcome.summary,
            }
        return entries

    def _write_exports(self) -> None:
        """Publish the exports. A RUNNING run becomes COMPLETE only through :meth:`_seal`.

        Nothing that would pass the root cap is published: a STOPPED run whose
        exports no longer fit keeps its receipts in ``state.sqlite`` and its
        earlier exports are demoted, never left claiming a status.
        """
        if self.store.run().status == "RUNNING":
            try:
                self._seal()
                return
            except StopError as exc:
                self.store.set_status("STOPPED", str(exc), phase_p._utc())
                self._arms = {a: _incomplete(a, f"run stopped: {exc}") for a in ("M", "T")}
        try:
            outputs, superseded = self._publish_outputs()
            run = self.store.run()
            receipt, manifest = self._documents(run.status, run.stop_reason, outputs, superseded)
            self._publish(RECEIPT, receipt)
            self._publish(MANIFEST, manifest)
        except StopError as exc:
            self._arms = {
                a: _incomplete(a, f"exports not published: {exc}")
                if self._arms.get(a, _incomplete(a, "not decoded")).status == "COMPLETE"
                else self._arms.get(a, _incomplete(a, "not decoded"))
                for a in ("M", "T")
            }
            self._demote(EXPORTS)

    def _seal(self) -> None:
        """The COMPLETE gate (protocol sections 1, 6, 11): every failure is a STOP.

        Both arms COMPLETE with exactly the frozen rows and locators; the bound
        Phase-P parent still exact; every export present and reproducing its
        hash; the whole root within its cap before and after the final write.
        """
        outputs, superseded = self._publish_outputs()
        self._check_outputs()
        receipt, manifest = self._documents("COMPLETE", None, outputs, superseded)
        self._recheck_parents("before completion")
        self._root_room(len(receipt) + len(manifest) + STORE_WRITE_RESERVE_BYTES, "sealing")
        self._publish(RECEIPT, receipt)
        self._publish(MANIFEST, manifest)
        self._check_exports()
        self._recheck_parents("at completion")
        self._root_room(STORE_WRITE_RESERVE_BYTES, "marking COMPLETE")
        self.store.set_status("COMPLETE", None, phase_p._utc())
        self._root_room(0, "after COMPLETE")

    def _request_lines(self) -> tuple[int, bytes]:
        attempts = self.store.attempts()
        return len(attempts), b"".join(canonical.canonical_bytes(a) + b"\n" for a in attempts)

    def _publish_outputs(self) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """The request receipt and each COMPLETE arm's outputs, every write under the cap."""
        self._publish(REQUESTS, self._request_lines()[1])
        outputs: dict[str, dict[str, Any]] = {}
        superseded: list[str] = []
        for arm, names in ARM_OUTPUTS.items():
            outcome = self._arms.get(arm, _incomplete(arm, "not decoded"))
            if outcome.status != "COMPLETE":  # an earlier output of a now-incomplete arm: keep
                superseded += self._demote(names)
                continue
            for name in names:
                raw = outcome.files[name]
                self._publish(name, raw)
                self._root_room(STORE_WRITE_RESERVE_BYTES, f"recording {name}")
                self.dstore.record_decoded(name, arm, outcome.records[name], raw, phase_p._utc())
                self._root_room(0, f"recorded {name}")
                outputs[name] = {"arm": arm, **_binding(raw)}
        return outputs, superseded

    def _records(self, name: str) -> list[Any]:
        lines = (self.root / name).read_bytes().splitlines()
        return [canonical.loads_bytes_strict(line) for line in lines]

    def _check_outputs(self) -> None:
        """Both arms COMPLETE; the published bundles hold exactly the frozen identities."""
        for arm in ("M", "T"):
            outcome = self._arms.get(arm, _incomplete(arm, "not decoded"))
            if outcome.status != "COMPLETE":
                raise StopError(f"arm {arm} is not COMPLETE: {outcome.reason}")
        try:
            locators = [r[dec.LOCATOR_FIELD] for r in self._records(M_OUTPUT)]
            rows = [(loc["source_file"], loc["row"]) for loc in locators]
            documents = self._records(T_DOCUMENTS)
            identities = [tuple(d["locator"]) for d in documents]
            statuses = {d["status"] for d in documents}
        except (OSError, canonical.CanonicalError, KeyError, TypeError) as exc:
            raise StopError(f"published outputs are unreadable: {exc}") from exc
        if rows != [(f.file, row) for f in self.dplan.m_files for row in range(*f.window)]:
            raise StopError("M output does not hold exactly the frozen window rows")
        frozen_identities = [loc.identity for g in self.dplan.t_files for loc in g.locators]
        if identities != frozen_identities or not statuses <= set(dec.T_TERMINAL):
            raise StopError("T output does not hold exactly the frozen locators")

    def _documents(
        self,
        run_status: str,
        stop_reason: str | None,
        outputs: dict[str, dict[str, Any]],
        superseded: list[str],
    ) -> tuple[bytes, bytes]:
        """The canonical receipt and manifest bytes for the exports now in the root."""
        run = self.store.run()
        count, lines = self._request_lines()
        rows = {r["op_id"]: r for r in self.store.operations()}
        store_outputs = self.store.outputs()
        both = all(self._arms.get(a, _incomplete(a, "")).status == "COMPLETE" for a in "MT")
        complete = run_status == "COMPLETE" and both
        receipt = self._doc(
            "phase_d_receipt",
            status="COMPLETE" if complete else "INCOMPLETE",
            run_status=run_status,
            stop_reason=stop_reason,
            created_utc=run.created_utc,
            finalized_utc=phase_p._utc(),
            arms=self._arm_entries(),
            source_files=[
                {
                    "arm": f.arm,
                    "ordinal": f.ordinal,
                    "file": f.file,
                    "remote_length": f.remote_length,
                    "strong_etag": f.strong_etag,
                    "canonical_url": self.plan.source.canonical_url(f.file),
                }
                for f in self.plan.files
            ],
            operations=[
                {
                    "op_id": op.op_id,
                    "seq": op.seq,
                    "arm": op.arm,
                    "ordinal": op.ordinal,
                    "kind": op.kind,
                    "range": [rows[op.op_id]["range_start"], rows[op.op_id]["range_end"]],
                    "status": rows[op.op_id]["status"],
                    "output": store_outputs.get(op.op_id),
                }
                for op in self.plan.operations
            ],
            totals=self._totals(),
            request_receipt={
                "path": REQUESTS,
                "lines": count,
                "bytes": len(lines),
                "sha256": _sha(lines),
            },
            outputs=outputs,
            limits=pd.LIMITS,
            human_review="NOT PERFORMED",
            scientific_scoring="NOT PERFORMED",
        )
        receipt_raw = canonical.canonical_bytes(receipt)
        retained = sorted(
            f"{folder}/{p.name}"
            for folder in ("payload", "tmp")
            for p in (self.root / folder).iterdir()
        )
        entries = {RECEIPT: _binding(receipt_raw)}
        for rel in [*retained, REQUESTS, *sorted(outputs), *superseded]:
            entries[rel] = _binding((self.root / rel).read_bytes())
        manifest = self._doc(
            "artifact_manifest",
            status=receipt["status"],
            receipt_digest=receipt["digest"],
            artifacts=entries,
        )
        return receipt_raw, canonical.canonical_bytes(manifest)

    def _verify_exports(self) -> None:
        self._check_exports()
        self._arms = {a: ArmOutcome(a, "COMPLETE", None) for a in ("M", "T")}

    def _check_exports(self) -> None:
        """Every COMPLETE export exists, reproduces its hash and is bound; the root fits."""
        try:
            manifest = canonical.loads_bytes_strict((self.root / MANIFEST).read_bytes())
            receipt = canonical.loads_bytes_strict((self.root / RECEIPT).read_bytes())
        except (OSError, canonical.CanonicalError) as exc:
            raise StopError(f"COMPLETE root exports are unreadable: {exc}") from exc
        for doc in (manifest, receipt):
            if canonical.self_digest(doc) != doc.get("digest") or doc.get("status") != "COMPLETE":
                raise StopError("COMPLETE root export self-digest/status mismatch")
            if doc.get("plan_digest") != self.dplan.digest:
                raise StopError("COMPLETE root exports bind a different plan")
        if manifest["receipt_digest"] != receipt["digest"]:
            raise StopError("manifest does not bind the receipt")
        for rel, entry in manifest["artifacts"].items():
            path = self.root / rel
            if not path.is_file() or phase_p._file_digest(path) != (
                entry["bytes"],
                entry["sha256"],
            ):
                raise StopError(f"COMPLETE root artifact {rel} drifted from its manifest")
        _, lines = self._request_lines()
        if _sha(lines) != receipt["request_receipt"]["sha256"]:
            raise StopError("request receipt differs from the attempt table")
        decoded = {
            name: {"arm": row["arm"], "bytes": row["bytes"], "sha256": row["sha256"]}
            for name, row in self.dstore.decoded().items()
        }
        required = {n for names in ARM_OUTPUTS.values() for n in names}
        if decoded != receipt["outputs"] or set(decoded) != required:
            raise StopError("decoded outputs differ from the receipt")
        if not {REQUESTS, RECEIPT, *required} <= set(manifest["artifacts"]):
            raise StopError("a required export is missing from the manifest")
        self._root_room(0, "COMPLETE root")

    def _phase_d_result(self) -> Result:
        run = self.store.run()
        arms = self._arm_entries()
        complete = run.status == "COMPLETE" and all(
            a["status"] == "COMPLETE" for a in arms.values()
        )
        return Result(
            status="COMPLETE" if complete else "INCOMPLETE",
            run_status=run.status,
            stop_reason=run.stop_reason,
            arms=arms,
            totals=self._totals(),
            root=str(self.root),
        )


# -- entry points ----------------------------------------------------------------


def live_root() -> Path:
    """THE fresh Phase-D execution root; never a caller input."""
    return Path(pd.EXECUTION_ROOT)


def parent_root() -> Path:
    """THE COMPLETE v4.1 Phase-P root (read-only parent); never a caller input."""
    return Path(pd.PHASE_P_ROOT)


def _check_dry_plan(plan: pd.PhaseDPlan) -> None:
    raw = (frozen.REPO_ROOT / pd.DRY_PLAN_PATH).read_bytes()
    parents = plan.parents
    if (len(raw), _sha(raw)) != (parents.dry_plan_bytes, parents.dry_plan_sha256):
        raise RefusedError("the committed dry plan differs from the plan's bound dry plan")
    dry = canonical.loads_bytes_strict(raw)
    if canonical.self_digest(dry) != dry.get("digest") or dry["digest"] != pd.DRY_PLAN_DIGEST:
        raise RefusedError("the committed dry plan is not the reviewed dry plan")


def run_live(*, confirm_plan_digest: str) -> Result:
    """Execute THE frozen Phase-D plan against the live source at THE Phase-D root."""
    if confirm_plan_digest != pd.PLAN_DIGEST:
        raise RefusedError("--confirm-plan-digest does not equal the frozen Phase-D plan digest")
    try:
        plan = pd.load_committed_plan()
    except (frozen.PlanError, OSError) as exc:
        raise RefusedError(str(exc)) from exc
    if plan.digest != confirm_plan_digest or plan.synthetic:
        raise RefusedError("committed plan differs from the confirmed plan")
    _check_dry_plan(plan)
    engine = _PhaseDEngine(
        plan,
        live_root(),
        parent_root(),
        tp.LiveHttpsTransport(policy=v41.HOSTS),
        sleep=time.sleep,
        clock=time.monotonic,
    )
    return engine.run()


def run_offline(
    root: Path,
    *,
    plan: pd.PhaseDPlan,
    parent: Path,
    transport: tp.Transport,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Result:
    """Run a SYNTHETIC Phase-D plan through the same engine (tests only)."""
    if (
        not plan.synthetic
        or plan.fetch.source.repository != frozen.SYNTHETIC_REPOSITORY
        or plan.fetch.source.revision == frozen.SOURCE_REVISION
        or plan.digest == pd.PLAN_DIGEST
    ):
        raise RefusedError("offline runs accept only synthetic fixture plans")
    if isinstance(transport, tp.LiveHttpsTransport):
        raise RefusedError("offline runs never use the live transport")
    for path in (root, parent):
        if any(phase_p._same_or_inside(path, fixed) for fixed in FROZEN_ROOTS):
            raise RefusedError("offline runs never use a frozen execution or parent root")
    return _PhaseDEngine(plan, root, parent, transport, sleep=sleep, clock=clock).run()


def inspect(root: Path) -> dict[str, Any]:
    """Read-only summary of a Phase-D root; never creates or modifies anything."""
    return phase_p.inspect(root)


# -- offline repository verification -------------------------------------------------


def _strict(rel: str) -> tuple[bytes, dict[str, Any]]:
    raw = (frozen.REPO_ROOT / rel).read_bytes()
    try:
        obj = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise verify.VerifyError(f"{rel}: {exc}") from exc
    if not isinstance(obj, dict) or canonical.self_digest(obj) != obj.get("digest"):
        raise verify.VerifyError(f"{rel}: self-digest mismatch")
    return raw, obj


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise verify.VerifyError(message)


def verify_repository() -> dict[str, Any]:
    """Recompute every committed Phase-D binding offline (v4.0 and v4.1 parents first)."""
    parent = v41.verify_repository()
    protocol = (frozen.REPO_ROOT / pd.PROTOCOL_PATH).read_bytes()
    protocol_sha = _sha(protocol)
    _require(protocol_sha == pd.PROTOCOL_SHA256, f"Phase-D protocol bytes drift: {protocol_sha}")
    _, freeze = _strict(pd.FREEZE_PATH)
    plan_raw, plan_obj = _strict(pd.PLAN_PATH)
    parent_raw, parent_binding = _strict(pd.PARENT_BINDING_PATH)
    adoption_raw, adoption = _strict(pd.ADOPTION_PATH)
    dry_raw, dry = _strict(pd.DRY_PLAN_PATH)
    _require(freeze["digest"] == pd.FREEZE_DIGEST, "Phase-D freeze digest drift")
    _require(freeze["protocol"]["sha256"] == protocol_sha, "freeze does not bind the protocol")
    for key, raw, digest in (
        ("phase_d_plan", plan_raw, pd.PLAN_DIGEST),
        ("phase_p_parent_binding", parent_raw, pd.PARENT_BINDING_DIGEST),
        ("scientific_adoption", adoption_raw, pd.ADOPTION_DIGEST),
    ):
        binding = freeze[key]
        _require(
            (binding["sha256"], binding["bytes"], binding["digest"])
            == (_sha(raw), len(raw), digest),
            f"freeze binding for {key} does not reproduce",
        )
    _require(dry["digest"] == pd.DRY_PLAN_DIGEST, "dry plan digest drift")
    _require(
        plan_obj["parents"]["dry_plan"]
        == {"path": pd.DRY_PLAN_PATH, **_binding(dry_raw), "digest": pd.DRY_PLAN_DIGEST}
        and freeze["dry_plan"] == plan_obj["parents"]["dry_plan"],
        "plan/freeze do not bind the committed dry plan",
    )
    _require(parent_binding["parents"] == plan_obj["parents"], "parent binding differs from plan")
    _require(parent_binding["phase_d_plan_digest"] == pd.PLAN_DIGEST, "parent binding plan")
    plan = pd.load_committed_plan()
    m_dry = [
        [(r["start"], r["end_exclusive"]) for r in f["request_ranges"]] for f in dry["files"]["M"]
    ]
    t_dry = [
        [(r["start"], r["end_exclusive"]) for r in f["request_ranges"]] for f in dry["files"]["T"]
    ]
    got_m = [
        [(o.range[0], o.range[1] + 1) for o in plan.ops_of("M", f.ordinal) if o.range]
        for f in plan.m_files
    ]
    got_t = [
        [(o.range[0], o.range[1] + 1) for o in plan.ops_of("T", g.ordinal) if o.range]
        for g in plan.t_files
    ]
    _require(got_m == m_dry and got_t == t_dry, "plan ranges differ from the reviewed dry plan")
    dry_locators = [[loc["identity"] for loc in f["locators"]] for f in dry["files"]["T"]]
    _require(
        dry_locators == [[list(loc.identity) for loc in g.locators] for g in plan.t_files],
        "plan locators differ from the reviewed dry plan",
    )
    _require(
        [(f["file"], f["window"]) for f in dry["files"]["M"]]
        == [(f.file, list(f.window)) for f in plan.m_files],
        "plan M windows differ from the reviewed dry plan",
    )
    _require(
        freeze["operational_limits"] == pd.LIMITS
        and freeze["network"] == v41.NETWORK
        and freeze["execution_root"] == pd.EXECUTION_ROOT
        and freeze["phase_p_root"] == pd.PHASE_P_ROOT,
        "Phase-D freeze limits/network/roots drift",
    )
    _require(
        adoption["adopted"]["scientific_identity_digest"] == frozen.SCIENTIFIC_IDENTITY_DIGEST
        and adoption["selection_digest"] == frozen.SELECTION_DIGEST
        and adoption["adopted"]["v4_1_phase_p_plan_digest"] == v41.PLAN_DIGEST,
        "Phase-D scientific adoption drift",
    )
    _require(
        pd.EXECUTION_ROOT not in (pd.PHASE_P_ROOT, frozen.EXECUTION_ROOT, phase_p.V3_ROOT),
        "the Phase-D root must be fresh",
    )
    ops = plan.fetch.operations

    def arm_bytes(arm: str) -> int:
        return sum(o.range[1] - o.range[0] + 1 for o in ops if o.arm == arm and o.range)

    return {
        "protocol_version": pd.PROTOCOL_VERSION,
        "protocol_sha256": protocol_sha,
        "freeze_digest": freeze["digest"],
        "plan_digest": plan.digest,
        "dry_plan_digest": pd.DRY_PLAN_DIGEST,
        "phase_p_parent_binding_digest": parent_binding["digest"],
        "scientific_adoption_digest": adoption["digest"],
        "parent_v4_1": {k: parent[k] for k in ("protocol_sha256", "freeze_digest", "plan_digest")},
        "phase_p_root": pd.PHASE_P_ROOT,
        "M_operations": sum(1 for o in ops if o.arm == "M"),
        "M_success_payload_bytes": arm_bytes("M"),
        "M_rows": sum(f.window[1] - f.window[0] for f in plan.m_files),
        "T_operations": sum(1 for o in ops if o.arm == "T"),
        "T_success_payload_bytes": arm_bytes("T"),
        "T_locators": sum(len(g.locators) for g in plan.t_files),
        "execution_root": pd.EXECUTION_ROOT,
        "execution_root_exists": Path(pd.EXECUTION_ROOT).exists(),
    }
