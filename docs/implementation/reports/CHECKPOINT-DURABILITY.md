# Checkpoint/artifact durability (synchronous baseline)

Branch: `fix/checkpoint-durability`. No network, no training, no installs,
no push, no merge. One product file changed (`src/xlm/artifacts/store.py`),
one test file added. Checkpoint publication funnels through
`ArtifactStore.publish_artifact`, so the store ordering below covers
checkpoints, acquisition artifacts, and CLI publishes together.

## 1. Commit(s)

Single commit on `fix/checkpoint-durability` (hash at commit time):
`src/xlm/artifacts/store.py` (+96/−4: durable ordering, dir-sync
abstraction, durability record) and `tests/test_artifact_durability.py`
(new, 15 tests). No other files touched.

## 2. Previous publication ordering

`publish_artifact` wrote payloads (buffered, no flush/fsync), then
`manifest.json` via `write_bytes` (no sync), then `_COMPLETED` via
`write_text` (no sync), then `rename` to the final name — with no fsync
of any file and no directory sync at any point. Checkpoint staging
(`_save_tensor`, JSON writes) likewise never synced; only the store copy
mattered, and it synced nothing. Measured: **zero fsync syscalls** for
small (8 B), multi-file (86 MB), and 1 MB torch-checkpoint publications
alike. A power loss could therefore leave `_COMPLETED` visible while
payloads or the manifest were still only in the page cache — atomic
rename without durability ordering.

## 3. New durability ordering

Per payload file: stream chunks (hash while writing, budget unchanged),
then flush + fsync before close. Then fsync the staging directory and
every created ancestor subdirectory (payload entries durable). Then
build the manifest (now also recording §5), write it durable, write
`_COMPLETED` durable — completion strictly last. Then fsync staging
again (manifest/marker entries), `mkdir` + fsync the destination
parent, re-check conflicts, atomic rename, fsync the parent. Any
failure aborts before return and the private staging tree is removed;
nothing is ever reported complete first. No payload is copied twice
(§8: streaming writes only gain flush + fsync syscalls).

This differs from the §5 sketch in one deliberate way: the staging
directory is synced twice (after payloads so the manifest's record is
honest, and after manifest/marker so their entries are durable before
the rename), rather than once.

## 4. File fsync semantics

`fsync_fileobj` = `flush()` + `os.fsync(fileno())` (verified working on
this Windows box). Every payload, the manifest, and the marker each get
exactly one fsync per publication — measured: small 3, multi-file 10,
checkpoint 4. fsync is per file, never per record/chunk.

## 5. Manifest/completion semantics

`_COMPLETED` cannot be durable while a required payload or the manifest
is not: each stage is synced before the next artifact exists, and the
marker is written last. Verification is unchanged and still
distinguishes complete (marker + manifest + sizes + SHA + file-set),
incomplete (missing marker → "incomplete"), and corrupt (size/SHA/set
mismatch) — proven by the torn-state matrix. The per-publish durability
facts ride in `cosmetic_metadata["durability"]`
(`file_sync`/`directory_sync`/`platform`), which is excluded from
`production_identity`/`output_identity` by construction, so artifact
identity is unaffected (asserted via `check_identity`).

## 6. POSIX directory durability

`sync_directory`: `os.open(path, O_RDONLY)` + `os.fsync` + close.
Applied to the staging dir (twice, §3), every created ancestor
subdirectory, and the destination parent (after mkdir and after
rename). A POSIX failure raises `DurabilityError` and aborts
publication fail-closed (tested by injection).

## 7. Windows durability behavior and remaining limitation

Measured on this box: opening a directory fails with `Permission
denied`, so **no directory-entry durability can be established with
the available APIs — and none is faked**. What holds on Windows:
file-content durability via fsync (works), rename atomicity via NTFS
journaling (OS-provided, timing not forceable), and fail-closed
verification on every partial state. Residual risk, stated precisely:
after power loss a fully-synced artifact's directory *entries* may not
have persisted even though all its bytes did; recovery then sees the
artifact as missing/incomplete (safe direction — never as valid with
lost bytes). `directory_sync_supported()` reports False and every
manifest records `"directory_sync": false, "platform": "nt"`. No
ctypes/kernel32 adventures were added: an unprovable guarantee would be
worse than an honest limitation. No new dependencies.

## 8. Failure-injection matrix

Each fault aborts publication with staging retired; afterwards the
destination is absent, or — only for a fault injected strictly after
the rename — present and fully verifying (both safe; present-but-invalid
never occurs):

| fault point | dest afterwards | verify |
|---|---|---|
| during payload write | absent | n/a |
| before payload fsync | absent | n/a |
| during manifest write | absent | fails |
| before marker write | absent | n/a |
| before rename | absent | fails |
| after rename, before parent sync | absent or fully valid | passes iff present |
| directory-sync failure | absent | n/a |
| torn states (marker-only, manifest-only, torn JSON, truncated payload) | — | all fail |

## 9. Focused test results

New file: 15 passed (fsync path spy, syscall counter, capability,
identity-neutral record, 7 injection points, 4 torn states, legacy
compat, nested subdirs, checkpoint publish). Related: `test_artifacts`,
`test_artifact_identity`, `test_ledger`, `test_checkpoint`,
`test_frozen_recovery` — 116 passed total with the new file, `-n 0`.
`ruff check`, `ruff format --check`, `mypy` clean on touched files.

## 10. Publication benchmark before/after

Same script, same box (fsync counts include only file+manifest+marker
syncs on Windows; dir syncs report False):

| artifact | before wall | after wall | fsyncs | bytes on disk |
|---|---|---|---|---|
| small (8 B payload) | 16.0 ms | 31.0 ms | 0 → 3 | 727 → 799 B (+72 B record) |
| multi-file (86 MB, 8 files) | 125 ms | 188 ms | 0 → 10 | +72 B |
| synthetic checkpoint (1 MB torch) | 31 ms | 31 ms | 0 → 4 | +72 B |

Cost is ~5–6 ms per fsync on this box; correctness takes precedence by
design and no durability operation was trimmed for speed.

## 11. Whether checkpoint completion is now safe enough for long research runs

For file-content durability: yes — no completion is reported before its
bytes are synced, and recovery fail-closes on every partial state, both
proven by tests. For entry durability on Windows: the residual risk is
fail-closed absence (§7), which is safe for correctness but means a
post-power-loss run may need to republish an artifact whose bytes
actually survived. That is acceptable for long runs (republish is
idempotent on identical inputs) but must not be mistaken for a
persistence guarantee of the directory entries.

## 12. Any prerequisite remaining before asynchronous checkpointing

Two, beyond this baseline: (a) a Windows story for directory-entry
durability if the project ever needs to promise entry persistence
(rather than fail-closed absence) — requires platform capabilities
beyond the stdlib and is explicitly out of scope here; (b) the SQLite
ledger (`record_artifact`) commits durably under SQLite defaults, but a
crash between artifact publication and ledger recording leaves a valid
artifact the ledger does not know — async checkpointing must reconcile
store-vs-ledger (re-verify on startup), which is currently neither
implemented nor tested.
