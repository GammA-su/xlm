# Essential-Web v4.1 T blinded package — exact commands and exit statuses

2026-09-30. Windows 11 build 26200; CPython 3.12.13; uv 0.12.19, offline,
locked, no sync. Working directory `F:\Project\xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`, starting HEAD
`65edfd0a9e3b33b2b98cc9423223e31506db59a5`. No network. Real evidence, not
fixtures, for the materialize, verify and scan commands; authored synthetic
fixtures for every pytest command.

Common prefix (`$U`):

```
uv run --offline --locked --no-sync --extra cpu --extra eval
```

Test-run environment: `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
PYTHONUTF8=1`.

Common arguments (`$A`):

```
--preparation docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/t-analysis-preparation.json
--entry-bindings docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/t-entry-bindings.json
--phase-d-root G:/Project/xlm-evidence-v4.1/essential-web-phase-d
--selection F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json
--m-seal-dir docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS
--package-root F:/XLM-Review/essential-web-v4.1-t
--output-dir docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE
```

## M seal (before and after)

```
$U python scripts/essential_web_m_analysis.py verify <the arguments in the M analysis COMMANDS.md>
```

Exit **0** before any T work and exit **0** again after sealing
([log](logs/m-seal-verify.log)); both times the recomputed digest is
`afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`.
`git status` shows no change under the M analysis directory. The T command
additionally requires the working `m_seal.json` and `artifact_manifest.json`
to be byte-identical to commit `65edfd0`.

## External root and access control

```
New-Item -ItemType Directory F:\XLM-Review\essential-web-v4.1-t
icacls F:\XLM-Review\essential-web-v4.1-t /inheritance:r /grant:r "GAMMA-DESKMAIN\gamma:(OI)(CI)F" "NT AUTHORITY\SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"
```

Exit **0**. The directory did not exist before. Every file written later
inherits exactly these three entries (checked on the key file and on a
reviewer file). NTFS volume.

## Materialization

```
$U python scripts/essential_web_t_package.py materialize $A
```

| Attempt | Exit | Result |
|---|---:|---|
| 1 | 1 | Refused: `T document line 1 disagrees with its committed entry binding` ([log](logs/materialize-attempt-1.log)). The committed binding hashes each stored record with its trailing newline; the builder hashed it without. Builder corrected. |
| 2 | 1 | Refused: `T document line 2 is out of frozen order` ([log](logs/materialize-attempt-2.log)). `t_ordinal` is the source-file ordinal, not the entry index. Builder corrected to require file ordinal, then ascending row. |
| 3 | 0 | Sealed ([log](logs/materialize.log)). Wall 2.121 s (whole process, one measurement). |

Both refusals happened while reading the sources, before a custodian key
existed: the external root was still empty after each (listed and
confirmed). The key was generated once, in attempt 3, and nothing was
derived from any other key. No assertion was weakened; both corrections
make the builder agree with the committed Phase-D result-review bindings.

## Seal verification (recomputes everything from the bound inputs and K)

```
$U python scripts/essential_web_t_package.py verify $A
```

Exit **0** ([log](logs/verify.log)). Every one of the 722 external files and
all eight Git bindings is byte-identical to the recomputation; the external
root holds no other file; package digest
`18c95b95699922db325626fd8776c2317231c51666f9bcc42898f5811dabc58d`.

## Independent leakage scan

```
$U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE/independent_scan.py
```

Exit **0** ([log](logs/independent-leakage-scan.log), counts only). This is
a second scan written separately from the builder's audit. It reads the
custodian key and the sealed sources to know what to look for and prints
counts, never values.

## Evidence-root preservation

A stat inventory (relative path, size, mtime in ns) of every file and
directory under both G: roots and the historical selection directory was
taken before the first source read and after the final verify; the two logs
are identical ([before](logs/roots-before.log),
[after](logs/roots-after.log)): Phase P 86 files / 3,241,704 bytes, Phase D
119 files / 206,319,966 bytes, selection directory 6 files / 1,059,048
bytes. The builder opened four Phase-D files read-only
(`sealed/t_selected_documents.jsonl`, `sealed/t_provenance.json`,
`t_acquisition_manifest.json`, `phase_d_receipt.json`) plus the selection
manifest; the M verifier opened its own three.

## Tests (authored synthetic fixtures only)

| Command | Exit | Result |
|---|---:|---|
| `$U python -m pytest tests/test_essential_web_t_package.py -n 0 -q -p no:cacheprovider` | 0 | 39 passed, 9.94 s ([log](logs/pytest-t-package.log)) |
| `$U python -m pytest tests/test_evidence_v21.py::test_selection_identity_constants tests/test_evidence_v21.py::test_scientific_namespace_stays_v2_0 tests/test_evidence_v22.py::test_v22_namespace_and_selection_unchanged tests/test_evidence_v41.py::test_scientific_identity_constants_are_unchanged tests/test_evidence_v41_phase_d.py::test_scientific_identity_is_preserved -n 0 -q -p no:cacheprovider` | 0 | 5 passed, 0.39 s ([log](logs/pytest-science-identity.log)) |
| `$U python -m pytest tests/test_evidence_v2_text.py -n 0 -q -p no:cacheprovider -k "secret or review_id or reviewer_orders or package or rubric or form or blind or collision"` | 0 | 7 passed, 21 deselected, 0.24 s ([log](logs/pytest-blinding-rubric.log)) |
| `$U python -m pytest tests/test_essential_web_m_analysis.py -n 0 -q -p no:cacheprovider` | 0 | 29 passed, 20.82 s ([log](logs/pytest-m-analysis.log)) |

`-p no:cacheprovider` only avoids a pytest cache-directory permission warning
on this machine; it changes no test selection. NOT RUN: the fast and full
offline selections, CUDA tests, network-authorized tests. A focused pass is
not a full-suite pass.

## Lint, format, types

Files: `src/xlm/data/evidence_v2/t_package.py`,
`scripts/essential_web_t_package.py`,
`tests/test_essential_web_t_package.py`, `independent_scan.py` in this
directory (mypy: the first two).

| Command | Exit | Result |
|---|---:|---|
| `$U ruff check <files>` | 0 | All checks passed ([log](logs/ruff-check.log)) |
| `$U ruff format --check <files>` | 0 | 4 files already formatted ([log](logs/ruff-format.log)) |
| `$U mypy --strict <2 files>` | 1 | Did not start: Windows application control blocks the compiled mypy module ([log](logs/mypy-compiled.log)) |
| `$U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict <2 files>` | 0 | Success: no issues found in 2 source files; same locked mypy from its pure-Python sources ([log](logs/mypy-interpreted.log)) |

An earlier `ruff check` exited 1 on one ambiguous variable name in
`independent_scan.py`; the name was changed and the scan rerun from the
committed file, so the scan log is from the committed script. The package
builder files were formatted and type-clean before attempt 3, so the code
hashes bound in the package are those of the committed files.
