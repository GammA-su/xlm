# C07 normalized-byte validation contract (v1 and v2)

2026-10-07. **READY FOR OPERATOR VALIDATION.** Source/test correction only. Bounded
real probe passes both known failing records; the complete 11-shard validation and the
production `input_preflight` are deliberately **NOT RUN** (operator step, below).
Supersedes the blocker in [PXX-C07-V2-NORMALIZED-COVERAGE](PXX-C07-V2-NORMALIZED-COVERAGE.md).

## Identity

- Source: `F:/Project/xlm-training-input-v2-coverage-fix`, `fix/c07-v2-normalized-coverage`,
  `a3574cbb7faccd9c82dfe1e883007db4cdbe46ac` (clean, unchanged).
- Work: `F:/Project/xlm-c07-normalized-contract`, `fix/c07-normalized-byte-contract`,
  created by `git worktree add -b ... a3574cbb...`. Initial `git rev-parse HEAD` =
  `a3574cbb7faccd9c82dfe1e883007db4cdbe46ac`, branch as named, `git status --short` empty.
  Final SHA: the commit containing this report (`git rev-parse HEAD`).
- No push, network, signing key, production write, C05/C06/count/selection/tokenization
  rerun, freeze change, training or CUDA work.

## Root cause: two coordinate systems

| Field | Coordinate | Producer |
|---|---|---|
| `byte_count` | `len(doc.text.encode())` = `CanonicalDocument.utf8_byte_count` (C02, original text) | `TokenShardWriter`, `tokenfast.index_line_parts` |
| `covered_bytes`, v1 spans, v2 derived spans | cumulative token payload bytes of `canonical_normalize(doc.text)` (NFC + CRLF/CR -> LF) | `ByteLevelBPETokenizer._offsets_from_encoding`, `tokenfast.tokenize_chunk` |

`tokenfast` refuses unless the full payload sum equals `len(canonical_normalize(text).encode())`,
then stores the emitted (possibly truncated) sum as `covered_bytes` beside the original
`byte_count`. NFC is not UTF-8-length-monotone (U+0344: 2 -> 4 bytes; U+212A: 3 -> 1),
CRLF contracts, lone CR is length-neutral. Any `covered <= byte_count` or
`EOS => covered == byte_count` check compares different spaces. The v1 training check
`span[1] <= byte_count` had the same defect.

## Invariant before / after

Before (`a3574cb`):
- v2: `0 <= covered <= byte_count`, `covered_bytes == reconstructed` (no type check; `5.0`
  passed), positions `in ([], [0])` / `([], [count-1])` (so `[False]` passed), zero-byte BOS/EOS.
- v1: per span `0 <= start <= end <= byte_count`; no contiguity, no `covered_bytes`, no framing.

After (shared helpers in `src/xlm/data/tokens.py`, v1 in `src/xlm/data/input_validation.py`):
- **Exact internal (unchanged or strengthened):** v2 token count/ceiling/read window,
  IDs inside the SHA-bound table, no explicit spans, `type(covered_bytes) is int` and equal
  to the sum of `token_bytes.u16[ids]`; v1 spans a list of `tokens` two-int lists (bool/float
  refused), contiguous from 0 (`start_i == end_{i-1}`, `end_i >= start_i`), final end ==
  `covered_bytes` (int).
- **Structural (both):** `bos_positions`/`eos_positions` are int lists equal to `[]`/`[0]`
  and `[]`/`[count-1]`; framing tokens are zero-length (v1 BOS `[0,0]`, EOS `[covered,covered]`).
- **Cross-coordinate (both), `check_normalized_coverage`:** `byte_count` int >= 0;
  `0 <= covered <= 3 * byte_count`; a complete record (EOS emitted) with `byte_count > 0`
  has `covered > 0`. Removed: `covered <= byte_count` (v2), `end <= byte_count` (v1).
- v2 reconstruction (`derived_byte_spans`) is unchanged and still exactly equal to v1.

## Why the new invariant is correct

`3` is `MAX_NORMALIZED_UTF8_GROWTH`: NFC = canonical composition of NFD; no canonical
composite is longer in UTF-8 than its two-code-point mapping (Hangul 6 -> 3), so NFC <= NFD
bytes; NFD maps code points independently, worst 3x (U+0390 2 -> 6); newline normalization
only replaces or deletes. Prefix coverage <= full coverage. The test proves both lemmas
exhaustively over all 1,112,064 scalar values of the pinned CPython 3.12.13 Unicode
15.0.0 database, shows tightness (U+1D160: 4 -> 12 bytes under NFC), and fuzzes 4,000
seeded mixed sequences. A Python/Unicode upgrade that breaks the bound fails that test.
Empty original text normalizes to empty (`covered == 0` is forced by the bound); nonempty
text normalizes nonempty, so a complete framed record must cover bytes.
No tighter general lower bound is claimed (NFC/CRLF contraction can reach 1/3).

## v1 and v2 behavior

- v2: admits every relation `covered <,==,> byte_count` within the bound; same refusals as
  before for corruption, plus non-int `covered_bytes`/positions and bound violations.
- v1: same semantics on explicit spans. Historical producer output (BPE, ByteTokenizer,
  `tokenfast` v1) is contiguous from 0 with zero-length framing, so valid v1 artifacts stay
  admitted; v1 records without `token_byte_spans` keep the old pass-through.
  A v1 shard with non-contiguous spans, mid-document structural IDs, or non-int
  coverage is now refused; no repository producer emits those.

## Tests (authored, offline)

`tests/test_c07_normalized_coverage.py` (65 cases). Fixtures build `CanonicalDocument`
from **unnormalized** text via `dataclasses.replace`; the shared helper (which normalizes)
is not used as an oracle. Covers unchanged text, NFC contraction (combining, singleton
U+212A), NFC expansion (U+0344, tight U+1D160), CRLF, lone CR, combined Unicode+newline
contraction and expansion, empty framed document, full BOS/EOS and selected/truncated
prefixes via the real writer slicing, v1 == v2 reconstruction and identical batcher
microbatches/state, all three relations (`<`, `==`, `>`) computed from real tokenization;
16 shared damages refused by v1 and v2 with the same pinned reason; 12 v1 span damages
(shape, types, origin, gap, overlap, reversal, BOS/EOS length); 13 v2 guards incl.
complete-without-coverage; an on-disk v1 index refusal.

## Commands and results

Prefix `U` = `uv run --offline --locked --extra cpu --extra eval`; pytest env
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`. Windows 11,
CPython 3.12.13, existing `pyproject.toml`/`uv.lock`/`.python-version` unchanged.

| Command | Exit | Result |
|---|---:|---|
| `U python -m pytest tests/test_c07_normalized_coverage.py -n 8 --dist=worksteal --max-worker-restart=0` | 0 | 65 passed |
| `U python -m pytest <17 modules> -n 16 --dist=worksteal --max-worker-restart=0 -m "not serial"` | 1 | 265 passed, 1 failed: `test_frozen_execution.py::test_snapshot_reuse_conflicts_and_paths` |
| same 17 modules `-n 0 -m serial` | 1 | 2 passed, 5 failed, 10 errors |
| same serial selection on unmodified `a3574cb` (src stashed) | 1 | **identical** failure set |
| `ruff format --check` / `ruff check` (changed src, test, script) | 0 | clean |
| `mypy --strict src/xlm/data/tokens.py src/xlm/data/input_validation.py` | 0 | no issues |
| `git diff --check` | 0 | clean |
| `U python scripts/c07_normalized_coverage_probe.py snapshot` (before/after) | 0 | identical; equal to the prior repair's snapshots modulo CRLF |
| `U python scripts/c07_normalized_coverage_probe.py bounded --records 8192` | 0 | below |

The 17 modules: `test_c07_normalized_coverage`, `test_c07_consumer_v2`,
`test_configurable_training`, `test_frozen_execution`, `test_mixture_stream`,
`test_parallel_tokens`, `test_performance_tokenization`, `test_prefetch`,
`test_sampling_trace`, `test_token_batches`, `test_token_map_cache`,
`test_token_publication`, `test_token_shards`, `test_trainer_data`, `test_trainer_mixture`,
`test_training_resolution`, `test_tokenize_freeze_fast`. Pre-existing failures are
`authored_tree`'s `len(files) < 400` source-tree ceiling (439 files) and one CLI snapshot
recipe-path error, reproduced at `a3574cb`; unrelated to C07. **The full offline
acceptance suite was NOT RUN** (focused/affected selection only).

## Bounded real probe (read-only, content-free)

`common_pile_prose` records 0..8191 (`validate_v2_ids` on every record, sequential
`tokens.bin` reads, contiguity checked), 0.44 s:
`covered < byte_count` 71, `==` 8105, `>` 16; zero refusals.

| Ordinal | token_count | byte_count | covered_bytes | BOS/EOS | Result |
|---:|---:|---:|---:|---|---|
| 46 | 30789 | 86722 | 86721 | [0] / [30788] | validated |
| 2332 | 1390 | 4602 | 4629 | [0] / [1389] | validated |

No text, document ID or other content printed. Evidence:
[C07-NORMALIZED-BYTE-CONTRACT](../evidence/C07-NORMALIZED-BYTE-CONTRACT/).
Snapshot hashes small files and the freeze directory; `tokens.bin`/`offsets.jsonl`
are compared by size and mtime only. Peak RSS for the probe was not recorded.

## Policy and artifact consequences: classification A

- `TrainingInputPolicy.binding()` is data-only (id, schema, ceilings); unchanged.
  Training-input-policy identity changes: **NO**.
- Shard fields, representation, `token_bytes.u16`, counters and manifests unchanged; the
  validator reads them in the producer's own coordinates. Shard regeneration: **NO**.
- Freeze/`training-data.json`/C05 attestation bind artifact digests, selection and policy
  binding, not consumer source code (`verify_training_freeze`, `resolve_training_input`
  identity: sources, mixture, exposure, C05 proof, policy binding, order). Freeze
  re-signing: **NO**.

## Requirement ledger

| Requirement | Status |
|---|---|
| v2 drop invalid cross-coordinate bound, keep internal checks | IMPLEMENTED, VERIFIED (authored) |
| v1 replace `span <= byte_count` with self-consistency | IMPLEMENTED, VERIFIED (authored) |
| Valid replacement cross-coordinate bound (3x) | IMPLEMENTED, VERIFIED (exhaustive, pinned Unicode) |
| Ordinals 46 and 2332 admitted | VERIFIED (bounded real probe) |
| Production artifacts untouched | VERIFIED (snapshot before/after) |
| Complete 11-shard validation / production preflight | NOT RUN (operator) |
| Full offline acceptance suite | NOT RUN |
| Representation / policy / freeze change | OUT OF SCOPE (not required) |

## Next operator commands (read-only)

```powershell
Set-Location F:/Project/xlm-c07-normalized-contract
uv run --offline --locked --extra cpu --extra eval python scripts/c07_normalized_coverage_probe.py validate; if ($LASTEXITCODE) { throw '11-shard C07 validation refused' }
uv run --offline --locked --extra cpu --extra eval python -m xlm.training.input_preflight --training-data G:/XLM/freeze/mix01-policy-v2/training-data.json --production; if ($LASTEXITCODE) { throw 'Production training consumer refused' }
```
