# C07-v2 normalized coverage: targeted repair blocked by a second invariant

Superseded 2026-10-07 by [PXX-C07-NORMALIZED-BYTE-CONTRACT](PXX-C07-NORMALIZED-BYTE-CONTRACT.md),
which replaces the retained upper bound. This report remains the historical record.

2026-10-07. **BLOCKED**, not production ready. The requested EOS-only repair is
implemented and passes its authored regressions, but complete real validation cannot
pass while retaining the explicitly required `covered_bytes <= byte_count` check.
Stopped further implementation/acceptance at the user's contradiction condition.

## Source and isolated repair

- Authoritative source: `F:/Project/xlm-training-input-v2`,
  `fix/training-input-policy-v2`, `947002d69d40c8278a62b5b25a5e323708ef337c`.
  Source remained clean and unchanged.
- Repair: `F:/Project/xlm-training-input-v2-coverage-fix`,
  `fix/c07-v2-normalized-coverage`, created exactly from that SHA. Initial
  `git rev-parse HEAD`, `git branch --show-current`, `git status --short` printed the
  required SHA, branch, and empty status. The commit containing this report is the
  partial repair's final identity; obtain its full SHA with `git rev-parse HEAD`.
- No push, signing key, production write, network request, tokenization, freeze or
  model training. Existing dependency trio unchanged.

## Content-free real evidence

Both records are in `G:/XLM/shards/mix01-policy-v2/common_pile_prose`.
Ordinals are zero-based. No document ID or corpus text was printed.

| Field | Original failure | Next failure after EOS repair |
|---|---:|---:|
| Record ordinal | 46 | 2332 |
| token_count | 30789 | 1390 |
| byte_count | 86722 | 4602 |
| covered_bytes | 86721 | 4629 |
| bos_positions | [0] | [0] |
| eos_positions | [30788] | [1389] |
| First / last token ID | 1 / 2 | 1 / 2 |
| First / last token byte length | 0 / 0 | 0 / 0 |
| Reconstructed covered sum | 86721 | 4629 |
| byte_count - covered_bytes | 1 | -27 |

Original exception: `v2 structural byte spans differ from v1 framing`.
Next exception: `v2 canonical byte coverage mismatch`.
The first 2332 records pass the repaired validator; the first component does not
complete. **Zero complete components verified in this run; 11-shard acceptance BLOCKED.**
The actual production `input_preflight` was **NOT RUN** after this stop condition;
it calls the same validator and is expected to encounter the second refusal.
This expectation is source analysis, not a claimed executed preflight result.

The first failure confirms the hypothesis. The second disproves the assumption
that normalized UTF-8 coverage is always bounded above by original UTF-8 bytes.
NFC is not byte-length-monotone: the authored U+0344 example is 2 original bytes
and 4 normalized bytes under the repository's `canonical_normalize`. This is a
synthetic explanation of a possible expansion, not a claim about characters in
the real record; its original corpus text was not inspected. Producer source
checks full coverage against normalized text, so expansion is consistent with
the producer's contract. Determining how to admit such records requires review
of the explicitly retained upper bound, including the legacy v1 validator.

## Source proof and exact change

`CanonicalDocument.__post_init__` requires `utf8_byte_count == len(text.encode('utf-8'))`.
The contract does not require `text` already equal its tokenizer-normalized version.
`ByteLevelBPETokenizer.encode_with_offsets` normalizes NFC and line endings before
accumulating vocabulary byte payloads; v1 EOS is at the normalized byte length.
`TokenShardWriter` stores the original document byte count and the summed emitted
coverage separately, including when the selected prefix excludes EOS.
`tokenfast.tokenize_chunk` checks the full payload sum against normalized text
before selection, then stores the emitted sum and original byte count separately.
Lone CR changes a byte to LF without changing length; CRLF reduces length.

The only executable production change is:

```diff
- if (bos and sizes[0] != 0) or (eos and (sizes[-1] != 0 or covered != nbytes)):
+ if (bos and sizes[0] != 0) or (eos and sizes[-1] != 0):
```

All ID, count/window, override, reconstruction equality, coverage upper/lower bound,
structural position and zero-byte checks remain. A comment and reconstruction
docstring distinguish normalized coverage from original text bytes.
No representation or producer change: cumulative spans, including EOS at
`[covered_bytes, covered_bytes]`, remain losslessly equivalent to v1. This is a
representation conclusion, **not** a claim that either validator admits every
NFC-expanding record. The legacy v1 input validator also bounds spans by `byte_count`.

## Authored regression evidence

The new test module preserves original text using a valid `CanonicalDocument`,
then produces the existing v1 shard. A documented authored conversion omits spans
and installs the exact vocabulary byte table for v2, independently of the fast
producer. The selected-prefix lookup is mocked; actual v1 slicing, writing,
reading, reconstruction, validation, packing and trace/state comparisons execute.
This fixture proves consumer logic, not live dataset compatibility.

Cases: already-normalized text, decomposed acute accent, CRLF, lone CR, combined
NFC/line endings, empty framed document, full EOS and selected prefixes. The empty
document has no positive-target proper prefix, so both parameter paths remain full
BOS/EOS. Every case compares IDs, spans, both training-index validators, all batch
fields (including trace metadata) and committed states. Twelve malformed-input
cases check the retained guards.

| Full authored record | Original bytes | Normalized covered bytes / EOS coordinate |
|---|---:|---:|
| normalized cafe-accent + LF | 6 | 6 |
| decomposed acute accent | 3 | 2 |
| a CRLF b | 4 | 3 |
| a CR b | 3 | 3 |
| combined | 8 | 6 |
| empty | 0 | 0 |

First draft test execution: 9 failed / 15 passed (three intended regressions plus
six fixture errors from a missing mock `digest`). Fixed the fixture only.
Against unchanged production source at 947002d: **3 failed / 21 passed**, precisely
the full NFC/CRLF/combined EOS cases. After the one-line repair: **24 passed, no skips**.
The shared prior helper normalized text before document creation, masking this bug
in previous shard-consumer fixtures.

## Environment, commands and limits

Windows PowerShell, Python 3.12.13, uv 0.12.19; fresh worktree-local environment
from `uv sync --offline --locked --extra cpu --extra eval` (exit 0, 103 packages,
20.94 s installation; cache-to-F: hardlink fallback copied files).
NumPy 2.5.3, tokenizers 0.23.2, CPU torch 2.14.0+cpu, psutil 7.2.2, pytest 9.1.1,
ruff 0.16.8, mypy 2.3.1. CPU/CUDA extras remain mutually exclusive; dependency and
installation policy unchanged. No CUDA work. All following commands use the repair
worktree. Evidence: [C07-V2-NORMALIZED-COVERAGE](../evidence/C07-V2-NORMALIZED-COVERAGE/).

Prefix `U` below means exactly `uv run --offline --locked --extra cpu --extra eval`.
For pytest, environment was explicitly:
`OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`. One process, no xdist controller.

| Command after U | Exit | Result |
|---|---:|---|
| `python scripts/c07_normalized_coverage_probe.py snapshot` | 0 | Before snapshot |
| `python scripts/c07_normalized_coverage_probe.py reproduce` (before fix) | 0 | First failure confirmed at 46 |
| `python -m pytest tests/test_c07_normalized_coverage.py -n 0 -q --basetemp C:/XLM-scratch/c07cov-before` | 1 | Initial fixture issue; 9 failed / 15 passed, 11.76 s |
| `python -m pytest tests/test_c07_normalized_coverage.py -n 0 -q --tb=short --basetemp C:/XLM-scratch/c07cov-before2` | 1 | 3 failed / 21 passed, 3.06 s |
| `python -m pytest tests/test_c07_normalized_coverage.py -n 0 -q --basetemp C:/XLM-scratch/c07cov-after` | 0 | 24 passed, 2.91 s |
| `python scripts/c07_normalized_coverage_probe.py validate` (after fix) | 1 | First component stopped on coverage mismatch; 1.04 s command wall time |
| `python scripts/c07_normalized_coverage_probe.py reproduce` (after fix) | 1 | Record 2332 diagnostic; explicit hypothesis contradiction |
| `ruff format src/xlm/data/tokens.py tests/test_c07_normalized_coverage.py scripts/c07_normalized_coverage_probe.py` | 0 | Two new files formatted; production file unchanged by formatter |
| `ruff check src/xlm/data/tokens.py tests/test_c07_normalized_coverage.py scripts/c07_normalized_coverage_probe.py` | 0 | Clean |
| `mypy --strict src/xlm/data/tokens.py` | 0 | One changed production source passes; unused-config note only |
| `python scripts/c07_normalized_coverage_probe.py snapshot` | 0 | After snapshot identical |

The authored expansion command was `U python -`, with this stdin:

```python
import json
from xlm.data.normalization import canonical_normalize
text = chr(0x0344)
normalized = canonical_normalize(text)
print(json.dumps(dict(fixture='authored U+0344', original_bytes=len(text.encode()), normalized_bytes=len(normalized.encode()))))
```

Exit 0, original 2 / normalized 4. An earlier PowerShell `python -c` form failed
with SyntaxError due to native quoting (exit 1); it performed no data work.
`git diff --check` passed (exit 0). Source status/HEAD were rechecked.
The first staged whitespace gate returned 2 because PowerShell evidence logs retained
CRLF. Evidence was converted to UTF-8/LF with trailing whitespace removed; diagnostic
values/results were preserved and before/after snapshots still matched. The staged
gate was then rerun; no tests were repeated for this log-format-only correction.

Probe limits: exact 11-component policy/header admission (24 GiB aggregate artifact
cap), bounded 2048-byte metadata lines, 1048576-token windows, 1800 s and 16 GiB RSS
checks, no data payload scratch, bounded content-free output. Since validation
stopped early, its completion resource summary did not execute; peak RSS/full-pass
throughput were **NOT MEASURED**, not inferred from the elapsed partial scan.
The before/after inventory contains 68 files totaling 16,170,082,696 bytes;
46 small files were hashed. Evidence occupied 63,021 bytes when measured.
No production payload was copied. Authored test scratch is under C:/XLM-scratch;
environment and evidence are in the repair worktree. Full-test resource use was
not measured because the full selection was not run.

## Requirement ledger and immutable artifacts

| Requirement | Status | Evidence / reason |
|---|---|---|
| Exact isolated starting SHA and clean source | VERIFIED | Git output |
| Original real failure / v1 semantics | VERIFIED | First diagnostic + authored v1 writer |
| EOS-only validator repair | IMPLEMENTED / VERIFIED | 24 authored cases; real scan advances beyond 46 |
| All other requested checks retained | VERIFIED | Diff and malformed-input tests |
| Full 11-shard validation | BLOCKED | Coverage exceeds original bytes at 2332 |
| Actual production input_preflight | NOT RUN | User's contradiction stop condition |
| Eight requested existing consumer modules | NOT RUN | Same stop condition; no full-selection claim |
| Ruff / changed-source strict mypy | VERIFIED | Exit 0 |
| git diff --check | VERIFIED | Exit 0 |
| Shards/freeze/training-data immutable | VERIFIED within snapshot scope | All 68 files' sizes/mtimes unchanged; SHA-256 of small files including signed JSON unchanged |
| Full payload checksum/signature audit | NOT RUN | No claim from metadata snapshots |
| Broader upper-bound repair | OUT OF SCOPE | Explicitly required guard; new authorization/review needed |
| Training, CUDA, full repository suite | NOT RUN / OUT OF SCOPE | Diagnostic consumer task only |

The source-code-only EOS correction changes no field signed into the shards,
freeze or training-data, and changes no policy identity. **Shards need regeneration:
NO for this change. Freeze needs re-signing: NO for this change.** This is not a
decision about an as-yet unreviewed broader repair. No artifact was regenerated or
re-signed. Freeze digest remains the user-provided production identity
`f25b1c8b035bf46cb9e07d7ae26b8579bc4f981217b3ec31e0551f8b982ab415`;
the unchanged-file snapshot is not a new signature verification.

Next prompt: review the newly demonstrated `covered_bytes > byte_count` case and
authorize a revised raw-versus-normalized byte-bound contract, including v1/v2
training validation, before resuming the requested complete acceptance selection.
Do not use this partial repair to launch training.

Exact read-only operator command to reproduce the remaining refusal:

```powershell
Set-Location F:/Project/xlm-training-input-v2-coverage-fix
uv run --offline --locked --extra cpu --extra eval python scripts/c07_normalized_coverage_probe.py validate
```

After the remaining contract issue is resolved, the requested production gate is:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m xlm.training.input_preflight --training-data G:/XLM/freeze/mix01-policy-v2/training-data.json --production
```

Source review also notes that this gate verifies HMAC signatures using the existing
trust configuration; it is read-only but not a key-free cryptographic verification
scheme. No trust secret was requested, printed or installed in this session.

**C07-V2 NORMALIZED-COVERAGE FIX BLOCKED**
