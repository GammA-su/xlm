# ArtifactStore ↔ ledger crash reconciliation

Branch: `fix/artifact-ledger-reconciliation`. No network, no training, no
installs, no push, no merge. Two product files changed
(`artifacts/ledger.py`, `cli/artifact_cmd.py`), one test file added.
`record_artifact` semantics and the ledger schema are untouched.

## 1. Commit hash

Single commit on `fix/artifact-ledger-reconciliation` (hash at commit
time): strengthened `rebuild_from_filesystem` (bounds, idempotent skip,
incomplete/conflict reporting, phase timing), new
`audit_ledger_references` + atomic `_record_if_missing`, extended
`artifact rebuild-ledger` CLI (bounds + audit flags),
`tests/test_artifact_reconcile.py` (18 tests), this report, STATUS entry.

## 2. Exact pre-fix crash window

`CheckpointManager.save_checkpoint` calls `store.publish_artifact(...)`
(line 304) and only afterwards `ledger.record_artifact(...)` (line 322).
A crash between the two leaves a valid, completed, fully verified
artifact in the store that no ledger row references — invisible to
`get_artifact` discovery, though safely re-verifiable from disk. The
window also covers any direct `publish_artifact` caller that records
separately (acquisition verifier, CLI publishes).

## 3. Reconciliation authority model

Store bytes + manifest + `_COMPLETED` + full `verify_artifact` are
authoritative proof an artifact physically exists; the ledger records
durable workflow knowledge. A ledger row never causes an invalid store
artifact to be trusted (every candidate is re-verified through the same
verifier normal consumption uses), and a store artifact is recorded only
after passing that verification. Transaction boundaries: SQLite
per-statement autocommit previously; row writes remain per-row
transactions (independent failure domains), while the new
`_record_if_missing` check-and-write shares one IMMEDIATE transaction so
concurrent reconcilers serialize instead of racing.

## 4. Store→ledger algorithm

Per kind directory (dot-directories, internal kinds skipped; symlinks
rejected per store rules): enumerate candidates; skip non-directories;
marker-without-manifest and manifest-without-marker go to
`incomplete_artifacts` (reported, never recorded); parse declared
payload bytes for the byte budget; full `verify_artifact`; compare the
verified manifest bytes/kind/resolved path against the existing row —
identical rows are skipped without touching them (true no-op, not even
timestamps); otherwise atomic insert-or-replace; kind/path mismatches
are collected as `conflicts`, never papered over. Duplicate runs
converge byte-identically.

## 5. Ledger→store handling

New `audit_ledger_references`: every row (deterministic ID order, SQL
`LIMIT` when capped) is re-verified against the store. Verdicts: `ok`
(valid, untouched), `healed` (valid again after an `unverifiable`
marking — status restored to `completed`), `unusable` (missing/corrupt
artifact, identity drift, or reference escaping the store root —
status flipped to `"unverifiable"`, identity columns preserved, row
never deleted). Nothing is ever repaired by inventing data; a later
republish heals the row through the normal `record_artifact` path.
State mutation is limited to the pre-existing `status` parameter —
no schema change, no ledger redesign.

## 6. Startup/caller integration

No automatic full-store scan was added: measurement (§11) shows even a
1000-artifact scan costs ~28 s, which no tiny command should pay on
every invocation. The narrowest correct caller is the existing explicit
entry point, extended: `artifact rebuild-ledger [--max-artifacts N]
[--max-verify-mib M] [--deadline-seconds S] [--audit-references]`
(defaults preserve full-scan behavior). Operator/startup recovery runs
that command; `CheckpointManager` needs no changes.

## 7. Bounds/security

Known artifact root only; candidates resolved and symlink-checked;
marker-less directories and stray files never become records; count cap
(SQL `LIMIT` + loop backstop), declared-byte cap (pre-verification
estimate; malicious under-declaration still fails verification),
monotonic deadline — every cap stops with `truncated: True` and a
reason, never silently. `bounded_children` entry caps retained.

## 8. Concurrency semantics

`artifact_id` PRIMARY KEY plus one IMMEDIATE check-and-write
transaction: two concurrent reconciles serialize — exactly one inserts,
the other observes and skips (proven by test: total inserts equal
artifact count, follow-up pass pure no-op). Concurrent heal/mark races
converge on the next audit since every mutation re-verifies first.

## 9. Crash-injection results

All deterministic, real filesystem, isolated roots: A (unknown →
recorded → reusable via ledger path) ✓; B (payloads only) ✓ refused;
C (manifest∅marker and marker∅manifest) ✓ refused + reported
incomplete; D (tampered payload) ✓ refused + reported corrupt; E
(existing row byte-identical after reconcile, incl. `created_at`) ✓;
F (missing + corrupt references → `unverifiable`, identity preserved,
repeat-stable) ✓; G (3 passes converge) ✓; H (24 unknowns across kinds)
✓. Conflict injection (same ID elsewhere) surfaces without touching the
good row ✓.

## 10. Checkpoint recovery result

Real `CheckpointManager.save_checkpoint` (tiny transformer, no
training) with `record_artifact` dropped mid-call — the exact crash
window — then reconcile, then discovery (`get_artifact` +
`verify_artifact`) and `load_checkpoint` into a fresh model: all green.
The checkpoint path needed zero changes; it inherits reconciliation
through the store.

## 11. 1/100/1000 artifact timings

Tiny single-file artifacts, same box (reconcile only, publishes excluded):

| total | wall | scan | verify | ledger |
|---|---|---|---|---|
| 1 | 0.03 s | 0.00 s | 0.02 s | 0.02 s |
| 100 | 3.31 s | 0.00 s | 1.28 s | 1.16 s |
| 1000 | 27.84 s | 0.00 s | 12.34 s | 8.13 s |

Ledger time is ~8 ms/row: one connection + WAL commit per row. Kept
deliberately (independent failure domains over batch atomicity); the
remedy if startup ever needs it is a batched transaction with per-row
savepoints, not skipping verification.

## 12. Whether async checkpointing prerequisites are now satisfied

Yes for the synchronous substrate: durable publication (prior task),
crash reconciliation (this task: unknown→recorded, idempotent,
convergent), idempotent ledger recording (atomic no-op duplicates),
valid restart discovery (ledger row → verified bytes → model reload,
proven). What remains is genuinely async work, not durability repair:
background snapshot threads, optimizer-state quiescing, GPU→CPU staging
discipline, and a recovery protocol for in-flight (never-announced)
snapshots — plus the known Windows entry-durability limitation (fail-
closed absence) carrying over unchanged.

## 13. Any remaining Windows-specific durability limitation

Unchanged from the durability task: file-content fsync holds; directory
fsync reports False; recovery treats missing/incomplete as absent, never
as valid. Reconciliation adds no new platform surface (SQLite + file
reads + `resolve()`), so there is nothing further to qualify.

## 14. Conflict risk with Astra P30B

Low. Touched: `ledger.py` (+reconcile/audit internals, additive result
keys), `artifact_cmd.py` (+CLI flags, additive output lines), one new
test file, report + status. Untested surface Astra might also touch:
`rebuild_from_filesystem` result shape (additive keys only — existing
key semantics preserved) and `rebuild-ledger` output lines (additive).
If Astra P30B rewrites the runs-table handling or CLI output text,
merging is mechanical; the atomic `_record_if_missing` is the one
semantic to preserve.

## Validation

New file: 18 tests passed (incl. CLI caller, concurrency, checkpoint,
bounds, 1/100/1000 benchmark with printed phases). Focused set with the
new file — artifacts, identity, durability, ledger, checkpoint, frozen
recovery: **133 passed, `-n 0`**. `ruff check`, `ruff format --check`,
`mypy` clean on all touched files. Giant suite not run per task scope
(Astra restructuring gates).
