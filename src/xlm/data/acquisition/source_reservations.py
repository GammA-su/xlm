"""Parent-process admission of source and worker output growth on their actual volumes."""

from __future__ import annotations

from pathlib import Path

from xlm.data.acquisition.source_growth import ProcessingGrowth
from xlm.data.acquisition.source_parquet import (
    ScratchBudget,
    ScratchCapError,
    SourceResume,
    TransferLimits,
    TransferResult,
    inspect_source,
)
from xlm.data.sources.essential_web_local import Unit


def reserve_metadata(directory: Path, growth: ProcessingGrowth, min_free: int) -> ScratchBudget:
    marker = directory / ".reserved-growth"
    if marker.exists():
        raise ScratchCapError("metadata reservation marker must not exist")
    budget = ScratchBudget(directory, 1, min_free)
    budget.cap_bytes = budget.occupied() + growth.run_peak
    if not budget.reserve("run-metadata", growth.run_peak, marker):
        raise ScratchCapError("operator metadata growth does not fit its volume")
    return budget


class SourceReservations:
    def __init__(
        self,
        scratch: ScratchBudget,
        output: ScratchBudget,
        limits: TransferLimits,
        growth: ProcessingGrowth,
        revision: str,
        metadata: ScratchBudget | None = None,
        *,
        offline: bool = False,
    ) -> None:
        self.scratch, self.output = scratch, output
        self.limits, self.growth, self.revision = limits, growth, revision
        self.offline = offline
        self.budgets = list(
            dict.fromkeys([scratch, output] + ([] if metadata is None else [metadata]))
        )
        self.classified: dict[str, SourceResume] = {}
        self.held: dict[str, list[tuple[ScratchBudget, str]]] = {}
        self.output_existing: dict[str, int] = {}
        self.paths: dict[tuple[int, str], Path] = {}

    def physical_fits(self) -> bool:
        groups: dict[int, list[ScratchBudget]] = {}
        for budget in self.budgets:
            budget.root.mkdir(parents=True, exist_ok=True)
            groups.setdefault(budget.root.stat().st_dev, []).append(budget)
        return all(
            min(b.free() for b in group) - sum(b.unwritten() for b in group)
            >= max(b.min_free_bytes for b in group)
            for group in groups.values()
        )

    def reserve(self, unit: Unit) -> bool:
        self.scratch.root.mkdir(parents=True, exist_ok=True)
        if not self.scratch.fits():
            return False
        proposals: list[tuple[ScratchBudget, str, int, Path]] = []
        if unit.url is not None:
            if unit.key not in self.classified:
                self.classified[unit.key] = inspect_source(
                    unit.url,
                    unit.partial,
                    unit.state,
                    name=unit.source_file,
                    limits=self.limits,
                    revision=self.revision,
                    expected_sha256=unit.expected_sha256,
                )
            resume = self.classified[unit.key]
            if self.offline and resume.kind != "local_complete_reuse":
                raise ScratchCapError(
                    "offline processing requires a verified complete local source"
                )
            unit.require_complete = resume.kind == "local_complete_reuse"
            proposals.append((self.scratch, unit.key, resume.final_reservation, unit.partial))
            proposals.extend(
                [
                    (self.scratch, unit.key + ":state", self.growth.state_bytes, unit.state),
                    (
                        self.scratch,
                        unit.key + ":state-temp",
                        self.growth.state_bytes,
                        unit.state.with_name(unit.state.name + ".tmp"),
                    ),
                ]
            )
        if unit.job is not None:
            known = (
                self.classified[unit.key].final_reservation
                if unit.require_complete
                else int((unit.identity_record or {}).get("length", self.limits.max_file_bytes))
            )
            amount = self.growth.for_source(known).processing_peak
            if unit.job.get("durable_path") is not None:
                # Raw copy temporary + published path coexist logically at link time;
                # identity final + temporary also coexist. Kept until publication.
                amount += 2 * self.limits.max_file_bytes + 2 * self.growth.metadata_bytes
            output_path = Path(unit.job["staging_dir"])
            existing = self.output._size(output_path)
            self.output_existing[unit.key] = existing
            proposals.append((self.output, unit.key + ":output", amount + existing, output_path))
        held: list[tuple[ScratchBudget, str]] = []
        try:
            for budget, key, amount, path in proposals:
                resolved = path.resolve()
                if not resolved.is_relative_to(budget.root.resolve()):
                    raise ScratchCapError("reservation path escapes its budget root")
                if any(
                    owner == id(budget)
                    and (resolved.is_relative_to(prior) or prior.is_relative_to(resolved))
                    for (owner, _), prior in self.paths.items()
                ):
                    raise ScratchCapError("processing reservations must not overlap")
                if not budget.reserve(key, amount, path):
                    for owner, token in held:
                        owner.release(token)
                        self.paths.pop((id(owner), token), None)
                    return False
                held.append((budget, key))
                self.paths[(id(budget), key)] = resolved
        except BaseException:
            for owner, token in held:
                owner.release(token)
                self.paths.pop((id(owner), token), None)
            raise
        if not self.physical_fits():
            for owner, token in held:
                owner.release(token)
                self.paths.pop((id(owner), token), None)
            return False
        self.held[unit.key] = held
        if unit.job is not None:
            unit.job["growth_reserved"] = self.growth.model_dump()
        return True

    def processing(self, unit: Unit, transfer: TransferResult | None) -> None:
        if unit.job is None:
            return
        length = (
            transfer.identity.length
            if transfer is not None
            else int((unit.identity_record or {})["length"])
        )
        growth = self.growth.for_source(length)
        amount = growth.processing_peak
        if unit.job.get("durable_path") is not None:
            amount += 2 * length + 2 * growth.metadata_bytes
        self.output.resize(unit.key + ":output", amount + self.output_existing[unit.key])
        if not self.physical_fits():
            raise ScratchCapError("combined physical growth would consume free-space reserve")
        unit.job["growth_reserved"] = growth.model_dump()

    def release(self, unit: Unit) -> None:
        for owner, token in self.held.pop(unit.key, []):
            owner.release(token)
            self.paths.pop((id(owner), token), None)
        self.output_existing.pop(unit.key, None)
