# P33 evidence interpretation

Read [the P33 report](../../reports/P33.md) before using these rates.

- `release_*` uses the released first optimizer LR. The certified product
  comparison is `release_eq_reference` versus `release_eq_gradient`, with
  `criteria: bit exact`. B16 and B32 fail the fixed parameter comparison.
- `release_final_b8` is the certified end-to-end configuration. Its resident
  counterpart is `release_final_compute_b8`. `release_final_b16/b32` are faster
  capacity observations, **not** certified replacements for the B8 reference.
- Older `final_full_*`, `final_compute_full_*`, `native_norm_*` and
  `prod_combined` contain the rejected native RMSNorm experiment. Those names
  are historical. The actual release-LR `prod_reference` comparison failed.
- Most non-`release_*` 50M performance/short-state records used an explicit
  counter-zero first LR. The final harness defaults to the release optimizer
  LR. Earlier successful comparisons do not supersede the final gate.
- Earliest `baseline_compute_b8` / `grad_sync_compute_b8` replayed the first
  update; later full/resident runs preload the advancing sequence. Use the
  latter for controlled input-path comparisons.
- A benchmark `status: VERIFIED` means that run completed, not that its
  numerical-equivalence comparison passed. Read the separate comparator JSON.
- `capacity_*` records controlled OOM exits under a 14.5-GiB allocator budget;
  they are not empty-card hardware capacity claims. WDDM's huge per-process
  memory sentinel in exception text is invalid and must not be used as data.
- `eq_checkpoint_disk_cap` is a real exit-125 resource abort. Snapshot retention
  inventories document the bounded cleanup; it was not relabeled as a pass.
- `final_gate_execution` preserves the first formatting failure.
  `format_followup` records the format-only correction and passing recheck.
- Measurement summaries use only telemetry samples inside each timing window.
  GPU utilization is sampled device telemetry, not SM occupancy or guaranteed
  integrated duty cycle. Host process CPU uses 100% for one logical core.

Commands, arguments, source hashes and measurement-time HEADs remain in the
raw records. Historical HEADs precede cleanup of this branch's own rejected
trial commits. Large diagnostic snapshots, profiler traces and checkpoints
remain local in ignored `artifacts/p33`; removed completed snapshots have
recorded hashes. No live data, model download or useful trained model is included.
