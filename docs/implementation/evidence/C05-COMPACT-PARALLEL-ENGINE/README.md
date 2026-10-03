# C05 compact parallel engine: authored benchmark evidence (PARTIAL)

Authored synthetic data only (`scripts/c05_compact_benchmark.py`); informational,
never a CI gate. Machine: Ryzen 7 5700X3D (8C/16T), 72 GB RAM, Windows 11, C: NVMe.

Series 2 was **cancelled by the operator on 2026-10-03** to free the machine for the
production run. Completed and valid:

* `results-scaling.jsonl` + `progress-run100k-w{1,2,4,8,16}.jsonl`: worker scaling,
  100,000 documents (complete; identical membership/group digest for all counts).
* `profile-100k.json`: per-document component profile, 3,000 production-sized docs.
* `results-batch.jsonl` + `progress-batch-r{64,128,512,1024}.jsonl`: batch rows
  64/128/512/1024 at 4 MiB, 16 workers (256 rows = `run100k-w16`).
* `gen-*.json`: authored corpus manifests (100k / 500k / 1M documents).

Incomplete (not run or aborted; no results exist):

* batch byte limits 1 MiB (aborted mid-run), 2 MiB (aborted mid-run), 8 MiB (not run);
* reference SQLite engine vs compact engine on 20k documents (not run);
* 500k and 1M scale runs (not run);
* RAM-ceiling memory plans 24/32/40/48/56 GiB (not run);
* production-sized compiled matcher (~7.6M patterns) RSS/shared-mmap runs (not run).
