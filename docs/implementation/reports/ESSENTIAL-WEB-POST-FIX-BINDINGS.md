# Essential-Web post-fix lineage and production-binding audit — 2026-09-30

**ESSENTIAL-WEB POST-FIX BINDINGS BLOCKED** — by one offline operator action
only (superseding admission). Every other binding is current.

Starting HEAD `5fb37c96b4bf8bcd661d7d0e11a8dd11d801dde7`, branch
`data/mix01-ultrax-6b`; `c643f2e` and `5fb37c9` are both ancestors. Narrow audit:
no network, fetch, probe, calibration, selector/mixture/quota change, approval on
the operator's behalf, or push. Unrelated dirty and untracked files are untouched.

## Does the stored admission survive the adapter change?

Adapter file `src/xlm/data/adapters/mix01_adapters.py` SHA-256:

| Commit | SHA-256 |
|---|---|
| `c643f2e` (admission prepared and approved) | `56ca4fb26e88b57409f9ba0e0743095a1ff552a83e702722a948c2115d3c42f8` |
| `5fb37c9` (current HEAD) | `3651ff2af4fcb46c207404e052caa59e1ec42f2ac5a429e5dec752f7e83a7ef6` |

It is the only file under `src`, `scripts`, `recipes` or `manifests` that differs
between the two commits. `adapter_id` (`essential_web_bnormal`), `adapter_version`
(`"1"`) and the selector identity are unchanged.

Two layers give two answers:

- **Enforced gate: still admits.** `AdmissionDecision` has no code-hash field.
  `AdmissionGate.evaluate` and `resolve_verified_production_admission` bind
  source, view, repository, revision, probe fingerprint, `adapter_id`, selector
  identity, review hashes, mitigation and resource contract. The probe
  fingerprint covers schema and file inventory, not adapter code. Read-only
  against `G:\XLM\xlm-home` at HEAD: `status` exit 0, all three views admitted,
  and the C04 verifier passes for each (decision attempt 1).
- **Approved package: stale.** The sealed prepared decisions the operator
  approved carried `source_binding.adapter_code_sha256 = 56ca4fb2…`, and
  `admit --prepared` compares that with the current file. Against HEAD the
  committed package differed in exactly that one key, so `admit` would refuse
  it. The approval was given for adapter code that is no longer the code.

Conclusion: the recorded admission is **stale with respect to the adapter-code
binding the operator approved**, and the fetch gate cannot see that, because the
hash is checked at approval time and not persisted in the decision. A
superseding operator admission is required before further production
acquisition. Persisting the hash in the decision would close this gap; that is a
contract change and was not made here.

## What was resealed

`prepare-admission` was rerun offline against the existing live schema receipt
and the real store (read-only; store file count and bytes identical before and
after, 94 files / 85,789,617 bytes). All three views pass the actual C04 verifier
in a temporary store with simulated approval; nothing was published to the real
store. The diff of `admission-decisions.json` is exactly three
`adapter_code_sha256` lines and three preparation timestamps. Fingerprints,
revision, selector binding, review hashes, plan hash and limits are unchanged.

New seal `decisions_sha256`:
`0c038d94b2c5e9579d13f4eacf5b39449af5daccfd3b994a517943134524ba7b`.

`future-admit.ps1` would overwrite `admission-record.json`, so supersession has
its own script, `future-readmit-after-adapter-fix.ps1`. It writes
`admission-record.attempt02.json`, refuses if that exists, and `admit` publishes
`admission_essential_web_<view>.attempt02` through `next_attempt`. Attempt 1 and
its record are preserved. It was parsed, not executed.

## Plan, fetch and raw artifact

| Item | Invalidated by the adapter fix? | Basis |
|---|---|---|
| A. Probe plan and authorization | No | `compute_behavioral_hash` covers source, view, revision, files, row ranges, sampling frame, limits, projection and window policy; no adapter code. Hash recomputes to `04db5c76…52b04c`, equals the authorization hash. |
| A. Fetch journal and receipt | No | Journal `COMPLETED`, 256 records, 9,511,581 bytes, 105 requests, bound to the plan hash. `data verify --no-publish` exit 0. |
| B. `raw_dataset` artifact | No | Produced by the fetcher before any adapter runs; published artifact unchanged. |
| C. Adaptation downstream of raw | Yes, and only this | Replayed offline with the corrected adapter. |
| D. Authorization of future plans | No | Same hash rule; see calibration below. |

Raw `selected_records.jsonl`: 2,793,802 bytes, 256 lines, SHA-256
`a1c2b8078b662af59d0a4f56a131710b8654c6ab5e9103d4d4760cb462b0a9f5` before and
after the resume. Raw, plan, journal and receipt timestamps are unchanged.

## Resume script and offline execution

`resume-probe-adaptation.ps1` contains no `data fetch`, no plan command and never
sets an offline flag to `0`. Every `uv` call is `--offline --locked --no-sync`.
It checks the plan hash and raw size/hash, runs `verify --no-publish`, uses
`calibration_adopt.py adapt` per view (reuse on exit 2, fail closed on exit 1,
refuse a nonempty partial directory), adapts the three views, reads status and
writes `measurement.json`, refusing to overwrite one. The failed first attempt
had left only an empty `essential_science` directory, which the staged writer
accepts. It does not set `TRANSFORMERS_OFFLINE`; that was set by the caller.

Executed once with `HF_HUB_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, `UV_OFFLINE=1` and all proxy variables pointed at a
closed local port. Exit 0 in 6.74 s.

| Pass | Accepted | Rejected | Unassigned | Other component | Malformed |
|---|---:|---:|---:|---:|---:|
| essential_science | 0 | 223 | 2 | 31 | 0 |
| essential_practical | 5 | 223 | 2 | 26 | 0 |
| essential_prose | 26 | 223 | 2 | 5 | 0 |

B-normal selector totals from the measurement: rejected 223, prose 26,
practical 5, unassigned 2, science 0. All equal the expected values.

## Probe measurement (live raw input, offline adaptation)

`G:\XLM\calib\essential-web-production\probe\measurement.json`, copied text-free
to [probe-measurement.json](../evidence/ESSENTIAL-WEB-POST-FIX-BINDINGS/probe-measurement.json).

Input and scanned rows 256; response-body bytes 9,511,581; decompressed bytes
1,924,727; requests 105; journal elapsed 33.62 s (includes any restart
downtime); malformed 0; retained 31 documents, 533,799 canonical bytes.

| Component | Documents | Canonical UTF-8 bytes | Characters | Estimated tokens (bytes/4; range /5–/3) | Transfer bytes per document | Transfer bytes per estimated token |
|---|---:|---:|---:|---|---:|---:|
| essential_science | 0 | 0 | 0 | 0 | n/a | n/a |
| essential_practical | 5 | 16,620 | 16,501 | 4,155 (3,324–5,540) | 1,902,316.2 | 2,289.19 |
| essential_prose | 26 | 517,179 | 502,911 | 129,294.75 (103,435.8–172,393) | 365,830.04 | 73.57 |

Each component bears the full shared transfer. Tokens are estimates from an
assumed bytes-per-token ratio, not tokenizer counts. This is a partial
diagnostic on one 256-row prefix of one file; zero science here is not a
capacity result. Peak process memory was not measured.

## Calibration binding

The digest `a6cab8cd58a127b77dd130249147f3541ac4575f3ea8369bf248879fd52e89b0`
is the canonical digest of `calibration-plan.json`: files, crawls, windows,
remote lengths, ETags, footer hashes, seed, selector identity and the source
binding without any code hash. It recomputes equal. All 26 frozen calibration
files match the manifest sealed at `57cb42f`; 16,384 rows, 8 × 2,048. Each of
the eight plan hashes recomputes and equals the `--authorization-hash` in
`future-calibration.ps1`. No calibration artifact binds adapter code, so nothing
was regenerated and sample membership is identical.

Informational records in the readiness freeze (`probe-plan.json`,
`source-provenance.json`, `inventory-adoption.json`, `dry-acquisition-plan.json`
and the BLOCKED refusal records) still name `56ca4fb2…`. They are historical,
sealed at `57cb42f`, and no gate reads that field; they were left unchanged.

## Validation

Windows 11, Python 3.12.13, uv 0.12.19, existing locked CPU/eval environment; no
dependency change. Prefix `uv run --offline --locked --no-sync --extra cpu
--extra eval`. Tests used one thread per worker and
`TOKENIZERS_PARALLELISM=false`. Test fixtures are authored and synthetic, apart
from the stored-row certification suite; they do not establish live
compatibility. The resume and the reseal read existing live artifacts.

| Check | Exit | Result |
|---|---:|---|
| Real-store `status` and C04 verifier, read-only | 0 | 3 views admitted at attempt 1 |
| Resume script, offline | 0 | Table above; 6.74 s |
| `prepare-admission` reseal | 0 | 3 views verified; real store unchanged |
| Calibration digest, 8 plan hashes, script authorization hashes | 0 | All equal |
| 11 test files, one xdist controller, `-n 16 --dist=worksteal`, `-m "not serial"` | 0 | 326 passed, 12.97 s |
| `tests/test_acquisition_bounds.py`, `-n 0`, short base temp | 0 | 35 passed, 31.93 s |
| Ruff check / format check / strict mypy, 9 files | 0 / 0 / 0 | Clean |
| PowerShell parser, new script | 0 | No errors |

This is a focused selection, not a fast or full suite pass. CUDA and network
tests were NOT RUN. The 11 files: bootstrap, source admission, readiness,
production selector, live certification (stored rows), fasttrack freeze,
acquisition plan, verifier, fetcher, calibration adopt, mix01 views. No test
exercises `resume-probe-adaptation.ps1` or `essential_web_measure.py` directly;
they were exercised by the real offline run above. Logs:
[logs](../evidence/ESSENTIAL-WEB-POST-FIX-BINDINGS/logs).

## Requirement ledger

| Requirement | Status |
|---|---|
| Ancestry, raw hash, plan/journal/receipt integrity | VERIFIED |
| Admission staleness determination | VERIFIED |
| Prepared decisions resealed to current adapter hash | IMPLEMENTED, VERIFIED |
| Superseding operator admission | NOT RUN; operator action, BLOCKING |
| Offline probe adaptation and measurement | VERIFIED |
| Calibration digest and authorization unchanged | VERIFIED |
| Live 16,384-row calibration | NOT RUN |
| Persisting adapter code hash in the enforced gate | OUT OF SCOPE; reported |
| C05 final-pool screening, official benchmark claims | BLOCKED by existing obligations |

## Next operator commands

From `F:\Project\xlm-data-ultrax`. First, offline:

```powershell
. .\scripts\operator_storage.ps1
& .\docs\implementation\evidence\ESSENTIAL-WEB-ADMISSION-BOOTSTRAP\future-readmit-after-adapter-fix.ps1 -Operator $env:USERNAME
```

Then, only after it exits 0, the live calibration (network; caps 8 × 128 MiB
response bodies and 8 × 330 requests; not run here):

```powershell
. .\scripts\operator_storage.ps1
& .\docs\implementation\evidence\ESSENTIAL-WEB-PRODUCTION-READINESS\future-calibration.ps1
```
