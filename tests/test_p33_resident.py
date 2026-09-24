"""The resident diagnostic uses real packing and preserves commit/rollback order."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import torch


@pytest.mark.cuda
def test_resident_sequence_matches_advancing_loader_and_partial_batches(tmp_path: Path) -> None:
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    path = Path(__file__).resolve().parents[1] / "scripts/benchmark_p33.py"
    spec = importlib.util.spec_from_file_location("p33_test_harness", path)
    assert spec is not None and spec.loader is not None
    benchmark: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    benchmark.LOCAL = tmp_path / "local"
    benchmark.EVIDENCE = tmp_path / "evidence"
    benchmark.fixture()
    with benchmark.batcher(8, 8192) as reference, benchmark.batcher(8, 8192) as source:
        resident = benchmark.ResidentBatcher(source, 3)
        counts = []
        for _ in range(3):
            before = reference.get_state()
            expected = reference.next_step_microbatches()
            actual = resident.next_step_microbatches()
            assert resident.get_state() == before
            resident.rollback()
            assert resident.get_state() == before
            assert resident.next_step_microbatches() is actual
            counts.append(len(actual))
            for cpu, gpu in zip(expected, actual, strict=True):
                for name in ("input_ids", "labels", "loss_mask", "position_ids"):
                    torch.testing.assert_close(
                        getattr(gpu, name).cpu(), getattr(cpu, name), atol=0, rtol=0
                    )
                assert gpu.segment_ids == cpu.segment_ids
                assert gpu.source_attribution == cpu.source_attribution
                torch.testing.assert_close(
                    gpu.metadata["input_attention_mask"].cpu(),
                    torch.tensor(cpu.metadata["input_attention_mask"], dtype=torch.bool),
                    atol=0,
                    rtol=0,
                )
            resident.commit()
            reference.commit()
            assert resident.get_state() == reference.get_state()
        assert counts == [2, 3, 3]
        with pytest.raises(RuntimeError, match="bounded resident fixture"):
            resident.next_step_microbatches()
