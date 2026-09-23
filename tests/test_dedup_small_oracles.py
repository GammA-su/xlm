"""Small versions of the same large lexical equivalence assertions.

The original scale tests retain their original counts and remain final-gate
requirements. Fifty-six records cover four cycles of the fourteen authored shapes.
"""

from pathlib import Path
from typing import Any

import pytest

import test_dedup_throughput as reference


@pytest.mark.parametrize(
    "oracle",
    [
        reference.test_engine_matches_reference,
        reference.test_sharded_output_equivalence,
        reference.test_output_shard_sizes_preserve_decisions,
        reference.test_exact_only_and_threshold_modes,
        pytest.param(reference.test_workers_and_layouts_agree, marks=pytest.mark.serial),
    ],
    ids=["engine", "sharded-output", "shard-boundaries", "thresholds", "workers-layouts"],
)
def test_small_lexical_equivalence(tmp_path: Path, oracle: Any) -> None:
    oracle(tmp_path, documents=56)
