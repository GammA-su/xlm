# Essential-Web admission-contract correction — 2026-09-30

**READY FOR OPERATOR ADMISSION**

Starting commit: `5b0cf7ae78325463533ae0957f9c03914b0ee1d6`, branch
`data/mix01-ultrax-6b`. This is an offline admission correction. No network,
new schema probe, calibration, acquisition, exclusion run, real admission,
selector/mixture/quota change or push occurred. Existing user changes were retained.

## Meaning discovered and amendment

The existing contract did **not** clearly define meaning A. In `policy.py`,
`BenchmarkContaminationRisk` was documented as “Contamination status regarding
standard evaluation benchmarks”, with `clean`, `suspect`, and
`disabled_pending_audit`. `AdmissionDecision.benchmark_risk` was an unrestricted
string defaulting to `clean`. `AdmissionGate.evaluate` admitted only `clean` and
explained refusals as benchmark-containing blends disabled pending component
audit. C04 itself never defined `clean`; C05 separately required excluding all
benchmark splits from gradient/tokenizer training. The bootstrap's notes and
review used the narrow blend-only interpretation, and the production-readiness
report explicitly described that interpretation as an unresolved judgment call.

Therefore the old review's contamination caveats were honest, but its unqualified
`clean` gate state was ambiguous and unsuitable as an admission contract. The
small versioned amendment is **`c04-benchmark-risk-v2`**:

- Add the enum value `suspect_with_mitigation`; reject arbitrary risk strings.
- Require that version, benchmark-review SHA-256, and a typed C05 mitigation
  binding. Essential additionally requires the source-rights, attribution and
  external-evidence review hashes, approved provenance and the resource contract.
- Require this state for `essential_science`, `essential_practical` and
  `essential_prose`. An old Essential `clean` decision refuses and must be resealed.
- Preserve other legacy decision defaults, but explicitly state that legacy
  `clean` does not establish zero contamination. No historical checkpoint is
  reclassified, and no comparison/scoring/selector behavior changes.

All runtime uses were inspected: the policy enum, decision model/gate and stored
production-admission resolver, generic `data admit`, bootstrap decision builder,
review checker and operator script, production-readiness loader, and their tests.
Publication/evaluation inspection covered `xlm.data.exclusion`, pool manifest/
freeze CLI, `xlm.evaluation.evidence`, `xlm.operator.final`, `final_cmd`, and report
collection. Report collection only carries exclusion IDs/counts; evaluation
coverage and `final-protected` exposure are not decontamination evidence.

## C05 binding and remaining limits

The data-only mitigation binds `xlm.data.exclusion` and the eventually frozen
Essential/Mix-01 canonical training pool, covering BLiMP, ARC-Easy, HellaSwag and
PIQA. Full-example and informative-span screening must run before tokenizer
fitting and gradient training. Acquisition/pretraining **eligibility** does not
assert that this step ran.

`xlm.data.exclusion.receipt.verify_benchmark_claim` requires the existing
`FinalExclusionReceipt` under protected policy: trusted issuer, signature, exact
input corpus, kept membership, exclusion policy and index. Expectations come
from independently supplied frozen lineage. `xlm.operator.final.verify_receipt`
also matches checkpoint/suite and separates evaluation integrity (`valid`) from
`official_benchmark_claims_allowed`. Without C05, the latter is false and the
classification is `possibly_contaminated`. Successful C05 verification is
`screened_with_limitations`, with `zero_contamination_proven=false`.

The existing matcher holds documents in memory, compares indexed spans against
documents and cannot prove absence of paraphrases. It has not been sized or run
for Mix-01. Final pool freeze still lacks exclusion integration and would record
`none_declared`; the new claim gate rejects that identity. The final-receipt CLI
currently supplies no frozen-pool receipt and therefore leaves official claims
disabled. A pool receipt cannot repair a checkpoint trained on a different,
unscreened membership. These limitations do not block source admission, but they
block training readiness and official benchmark claims. No real exclusion
receipt was created.

## Existing live evidence and prepared decisions

Read-only source receipt:
`G:\XLM\calib\essential-web-production\schema-probe\schema-probe-20260930T115739Z.receipt.json`.
It is `ACCESSIBLE`, `real_observed`, with 8 requests and 238,373 response-body
bytes from the **previous live run**, not this session. Repository
`EssentialAI/essential-web-v1.0`, revision
`ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`, declared license `odc-by`, schema digest
`e6219f02e11c98e9841047084accfea7181e1c7663233662af902b90370b1a74`.
The receipt's digest and all three immutable stored probe artifacts were checked.

| View | Bound live probe fingerprint | Prepared status |
|---|---|---|
| `essential_science` | `2f64b00d35446dc3623d168edcb053a0131c43938b309a74f57110f541317ea9` | VERIFIED offline; NOT RECORDED |
| `essential_practical` | `73035aa5727ff54ecebcf3eca372c13216f46566dde0497b8ac79b3eb0bd3840` | VERIFIED offline; NOT RECORDED |
| `essential_prose` | `7448f2f1f7ac8309f77b1d3e14bded496756400065187b5b09cb8e1a56e0286d` | VERIFIED offline; NOT RECORDED |

Every decision binds the repository/revision, live fingerprint, exact
`essential_web_bnormal` adapter and frozen B-normal selector identity, four review
hashes, mitigation and C04/C13 resource contract. The package also binds the
existing production plan hash and exact resource limits. No plan was executed.
`AdmissionGate.evaluate` and `resolve_verified_production_admission` passed for
all three views in a temporary store using simulated approval. Prepared decisions
retain `operator_approved=false`. A read-only real-store status check returned
exit 1 as expected, with “No operator admission decision recorded” for all three.

Decisions and their SHA-256 seal are in
[the bootstrap package](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/admission-decisions.json).
The operator script now verifies this seal and compares the prepared decision
with current evidence/reviews before recording approval. The bootstrap artifact
manifest is resealed with explicit version/parent lineage. Historical
`readiness.json`, `COMMANDS.md` and old logs remain historical; this report and
the v2 decision seal supersede their pending-schema status.

## Validation and environment

Windows, Python **3.12.13**, uv **0.12.19**, existing locked CPU/eval environment.
`pyproject.toml`, `uv.lock`, `.python-version`, CPU/CUDA installation policy and
dependency graph were not changed. Every Python/tool command used
`uv run --offline --locked --no-sync --extra cpu --extra eval` (abbreviated `$U`).
Tests used one process (`-n 0`), `HF_HUB_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`,
`OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`, and
`TOKENIZERS_PARALLELISM=false`.

Tests use authored synthetic fixtures, including explicitly real-shaped test
records used only to exercise gates. They do not establish live compatibility.
The separate prepared-decision verification reads existing real probe metadata;
it does not contact the dataset or read corpus/benchmark text.

| Run | Exit | Observed outcome |
|---|---:|---|
| Initial bootstrap/exclusion-receipt/operator tests | 1 | 98 passed, 1 new fixture failed: wrong fingerprint was shorter than artifact-store minimum. Corrected to a foreign 64-character digest. |
| Twelve-file requested/regression selection | 1 | 308 passed, 4 failed: three readiness fixtures still expected legacy Essential `clean`; migrated to v2. One unrelated reports CLI test could not load PyTorch `_C` because Windows application-control policy blocked the DLL. |
| Focused bootstrap/readiness/exclusion-receipt/operator after migration | 1 | 133 passed, 1 stale artifact expectation: committed decision fingerprint was expected to be pending, but now correctly binds the existing live record. Migrated expectation and added seal validation. |
| Exact repaired package test plus sealed operator workflow | 0 | 2 passed, 1.58 s. No retries without a concrete repair. |
| Final bootstrap suite after binding adapter code and review permission | 0 | 51 passed, 9.89 s; includes every new refusal case and sealed workflow. |
| Actual C04 verifier, three existing live records in temporary store | 0 | All 3 passed; real admission not published. |
| Real-store read-only status | 1 expected | All 3 pending explicit operator approval. |
| Ruff final / format check, 13 changed Python files | 0 / 0 | Clean / all formatted. Earlier formatting findings were corrected and retained in logs. |
| Strict mypy, same 13 files | 0 | No issues; compiled installed mypy worked. |

The requested C04, C05/exclusion, bootstrap, admission verifier and production
readiness checks pass across these focused runs. The broader report CLI test
remains **BLOCKED by the environment**, not passed or skipped. Fast/full repository
acceptance, CUDA and network tests were **NOT RUN**. No full-suite pass is claimed.

Exact commands and file selections are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-ADMISSION-CONTRACT-V2/COMMANDS.md).
Measured focused run: 13.157 s wall time, sampled peak process-tree RSS
246,935,552 bytes (100 ms sampling). Measured offline reseal: 0.656 s and
87,638,016 bytes sampled peak process-tree RSS. These are validation overhead,
not throughput or training measurements. Evidence sizes and byte-integrity
checks are in the evidence directory; peak scratch disk was not instrumented.
There are no new GPU or acquisition performance measurements.

## Requirement ledger

| Requirement | Status | Evidence or remaining work |
|---|---|---|
| Discover precise existing risk semantics | VERIFIED | Ambiguous definition; narrow blend interpretation existed only in notes |
| Versioned typed mitigated state and refusal paths | IMPLEMENTED / VERIFIED | Model, C04 gate, bootstrap and synthetic regressions |
| Existing real schema-probe requirement | VERIFIED | Prior receipt and verified stored artifacts, no new probe |
| Three source/review/selector/resource-bound decisions | IMPLEMENTED / VERIFIED | Resealed preparation; actual verifier in temporary store |
| Real operator admission | NOT RUN | Only operator approval remains for C04 |
| C05 matching and signed receipt interface | IMPLEMENTED / VERIFIED | Authored fixtures, existing matcher and new exact-pool claim verifier |
| Final Mix-01 C05 integration/screening | BLOCKED / NOT RUN | Integration and bounded sizing required before training/official claims |
| Official benchmark claim gate | IMPLEMENTED / VERIFIED | Missing/mismatched/development C05 refuses; admission never proves cleanliness |
| Extra reports CLI regression | BLOCKED | Windows application-control denial of PyTorch DLL |
| Full acceptance, CUDA, new live probe/calibration/acquisition | NOT RUN | Outside this focused correction |
| Selector/source/revision/mixture/quota changes, push | OUT OF SCOPE | Unchanged/not performed |

## Files changed

- Contracts/docs: `CONTRACTS.md`, `EVALUATION_POLICY.md`,
  `docs/implementation/STATUS.md`, `reports/P07.md`,
  `reports/ESSENTIAL-WEB-PRODUCTION-READINESS.md`, this report.
- Runtime: `src/xlm/data/sources/{policy,admission,essential_web_bootstrap}.py`,
  `src/xlm/data/exclusion/receipt.py`, `src/xlm/operator/final.py`,
  `src/xlm/cli/{data_cmd,final_cmd}.py`, `scripts/essential_web_bootstrap.py`.
- Tests: `test_essential_web_bootstrap.py`, `test_essential_web_readiness.py`,
  `test_essential_web_production_selector.py`, `test_exclusion_receipt.py`,
  `test_operator.py`.
- Bootstrap evidence: risk review, review manifest, decisions and new seal,
  operator script, artifact manifest. New `ESSENTIAL-WEB-ADMISSION-CONTRACT-V2`
  evidence holds commands, logs, validation/resource records and an exact copy
  of the existing text-free live schema receipt.

## Next operator command

From `F:\Project\xlm-data-ultrax`, after reviewing the sealed decisions:

```powershell
. .\scripts\operator_storage.ps1
& .\docs\implementation\evidence\ESSENTIAL-WEB-ADMISSION-BOOTSTRAP\future-admit.ps1 -Operator $env:USERNAME
```

This is the real admission action and was not run here. The schema probe is
already satisfied. Later training-pool integration must apply bounded C05
exclusion and bind its protected receipt before official benchmark claims.
