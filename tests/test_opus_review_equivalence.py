"""Independent randomized serializer/MinHash and near-budget lease checks."""

from __future__ import annotations

import multiprocessing
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from test_acquisition_leases import manager
from xlm.data.acquisition.disk import CapacityLease
from xlm.data.dedup import minhash
from xlm.data.sources.transport import BudgetExhaustedError


def _budget_process(root: str, barrier: Any, queue: Any) -> None:
    capacity = manager(Path(root), 100_001)
    lease = CapacityLease(capacity, "transfer")
    actual = 0
    try:
        try:
            actual = lease.take(13_001)
            lease.commit(actual)
        except BudgetExhaustedError:
            pass
        snapshot = capacity.snapshot()
        assert snapshot["transferred_bytes"] + snapshot.get("reserved_transfer_bytes", 0) <= 100_001
        barrier.wait(timeout=30)
    finally:
        lease.close()
    queue.put(actual)


@pytest.mark.parametrize("workers", [1, 2, 4, 8, 16])
@pytest.mark.serial
def test_spawned_processes_share_exact_budget(tmp_path: Path, workers: int) -> None:
    context = multiprocessing.get_context("spawn")
    capacity = manager(tmp_path, 100_001)
    barrier, queue = context.Barrier(workers), context.Queue()
    processes = [
        context.Process(target=_budget_process, args=(str(tmp_path), barrier, queue))
        for _ in range(workers)
    ]
    try:
        for process in processes:
            process.start()
        used = sum(queue.get(timeout=60) for _ in processes)
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        snapshot = capacity.snapshot()
        assert snapshot["transferred_bytes"] == used <= 100_001
        assert snapshot["reserved_transfer_bytes"] == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=10)


@pytest.mark.parametrize("workers", [1, 2, 4, 8, 16])
def test_independent_journals_share_exact_budget(tmp_path: Path, workers: int) -> None:
    limit = 100_001
    accounts = [manager(tmp_path, limit) for _ in range(workers)]
    barrier = threading.Barrier(workers)

    def work(index: int) -> int:
        capacity = accounts[index]
        lease = CapacityLease(capacity, "transfer")
        actual = 0
        try:
            try:
                actual = lease.take(13_001)
                lease.commit(actual)
            except BudgetExhaustedError:
                pass
            snapshot = capacity.snapshot()
            assert (
                snapshot["transferred_bytes"] + snapshot.get("reserved_transfer_bytes", 0) <= limit
            )
            barrier.wait(timeout=30)
        finally:
            lease.close()
        return actual

    with ThreadPoolExecutor(max_workers=workers) as pool:
        used = sum(pool.map(work, range(workers)))
    assert accounts[0].snapshot()["transferred_bytes"] == used <= limit
    assert accounts[0].snapshot()["reserved_transfer_bytes"] == 0


@pytest.mark.parametrize(
    "amount", [0, 1, 65535, 65536, 65537, 1048575, 1048576, 1048577, 16777215, 16777216, 16777217]
)
def test_exact_lease_boundary_and_one_beyond(tmp_path: Path, amount: int) -> None:
    capacity = manager(tmp_path, max(1, amount))
    lease = CapacityLease(capacity, "transfer")
    lease.consume(amount)
    lease.close()
    assert capacity.snapshot()["transferred_bytes"] == amount
    if amount:
        with pytest.raises(BudgetExhaustedError):
            lease.consume(1)
        assert capacity.snapshot()["transferred_bytes"] == amount


@pytest.mark.parametrize("permutations", [8, 16, 64, 128, 256, 1024])
def test_three_minhash_backends_randomized(
    permutations: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(71 + permutations)
    config = minhash.MinHashConfig(num_permutations=permutations, bands=8)
    params = minhash._permutation_params(config)
    prime = minhash._MERSENNE_PRIME
    cases = [set(), {0}, {2**64 - 1}, {0, 1, prime - 1, prime, prime + 1, 2**64 - 1}]
    cases += [{rng.getrandbits(64) for _ in range(rng.randrange(1, 160))} for _ in range(80)]
    cases += [{rng.getrandbits(64) for _ in range(n)} for n in (2047, 2048, 2049, 4097)]
    hasher = minhash.MinHasher(config)
    for values in cases:
        if not values:
            assert hasher.signature_from_hashes(values) == [2**64 - 1] * permutations
            continue
        expected = minhash._signature_python(values, params)
        assert minhash._signature_vectorized(values, params) == expected
        assert minhash._signature_arrow(values, params) == expected
        assert hasher.signature_from_hashes(set(list(values) * 2)) == expected
    monkeypatch.setattr(minhash, "_signature_vectorized", lambda *args: None)
    assert hasher.signature_from_hashes(cases[-1]) == minhash._signature_python(cases[-1], params)
