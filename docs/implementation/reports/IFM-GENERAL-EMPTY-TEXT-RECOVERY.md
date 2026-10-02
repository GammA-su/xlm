# IFM General empty-text recovery (p02 rank 0, admission repair)

Status: **IFM GENERAL SEMANTIC RECOVERY READY FOR OPERATOR REVIEW.**

Nothing was published, admitted, authorized or run. No operator artifact
changed.

- Branch: `fix/ifm-production-bounds`, worktree `F:\Project\xlm-ifm-bounds`.
- Base: `2c0f91a`.
- Operator roots (read only): data `G:\XLM`, scratch `C:\XLM-scratch`, store
  `G:\XLM\xlm-home`.
- Network: none. Every command ran offline (`uv run --offline --locked`,
  `HF_HUB_OFFLINE=1`).
- Environment: Windows 11, CPython 3.12.13, uv 0.12.19, pyarrow 25.0.1.

Evidence: [`evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/`](../evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/).
No corpus text appears in it or here. The evidence holds only counts, row
coordinates, lengths, hashes and text-free rejection reasons.

## 1. History snapshot (immutable; `history_before.json` = `history_after.json`)

General p02 is digest `6c0dfe5a3474978684d294cf8b9dffa616d6cf963b3bfdcd3ecaaea89ec79939`.
It was authorized by GammA at 2026-10-01T19:56:49.849783Z.

| p02 file | bytes | SHA-256 | mtime (UTC) |
|---|---:|---|---|
| plan.json | 9,621 | `8ce5f1faf6c13b3bf563019522ed570301e70fcb4ff568e50c977581b86e0c47` | 19:43:13.906740 |
| authorization.json | 166 | `5314a29a0f752c7b3a140dbacecf459e9c37c3559c5a28bed1089a0d11b49b42` | 19:56:49.849783 |
| acquisition.plan.json | 2,536 | `5ac0bf5dc26abe73cdf799d4a5666d5f9a4a89f723c6cde165455a63cf2b7091` | 19:56:49.920027 |
| events.jsonl | 753 | `f1851a739e4b84bb5c695043dd41fbd7480fca7950d19c2012d99e3f402af0b3` | 20:00:50.476148 |
| performance-00.json | 4,785 | `a0f171b7f9057eccdf1bd9552a47052802d9ab51412f26dce22e334ad9e28546` | 20:00:50.477707 |

**p01 is unchanged.** Its five files keep the hashes recorded in
[IFM-PRODUCTION-BOUND-RECOVERY](IFM-PRODUCTION-BOUND-RECOVERY.md) §1 (plan
`fce481db…a186a0`). The same holds for Planning p01 (`89ee0c60…dadaea`),
Planning p02 (`2bdd2a06…1c83`), both transport policies and General
`sufficiency.json` (`095010ab…37f8`, written by the operator at
06:32:16Z on 2026-10-02).

**The p02 run receipt.** `performance-00.json` has digest
`9bdfe633fa6b589fca37c86cd7d0705f422876092d6ccc49c8ab604aa10669db`.

- Root failure: f00000 `MissingFieldError` at `mix01_adapters.py:67 in _require`.
- Restart classes: sealed_skip 1, local_processing_retry 1, all others 0.

**Sealed rank 1 (f00001)** is sealed exactly once, in `canonical/ifm_general/p02/f00001`.

| field | value |
|---|---|
| receipt | `receipt.json` digest `eec4fc295c42b7449187a57a75169668e8ae5c3b75b5f31e0b082b6d842079b3` (file SHA-256 `bea2d5d5…0dc4`) |
| file | `general/general_full.chunk0-bdbff8a5c6-00146.parquet` |
| raw | 2,014,409,401 B, SHA-256 `09037215b11fd9070c5fd5b3d273efcb5d583b965f5babefcb3c9e9dc9fa7a0e` |
| rows / documents / rejected | 328,568 / 328,568 / 0 |
| canonical bytes / est. tokens | 4,842,744,134 / 1,210,686,033 |
| documents.jsonl | 5,195,802,585 B, SHA-256 `df33f69fbcb7eb46d9c06bd32c08104d7a6490ea0facf9dd1e63cbce257c6e1e` |
| transfer | 2,014,409,401 B, 2 requests |

**Accounting.**

- p01 `639b188a…c6d2`: 0 units.
- p02 `ed30db7d…d8ae`: 1 of 2 units.
- Sufficiency `1056e556…95c4`: **INCOMPLETE**, unresolved `{"2": [0]}`, acquired
  4,842,744,134 ≥ 660,000,000 required, `next_cursor` 2.

**Retained f00000 is local and complete. No download is needed.**

- The durable copy `G:\XLM\acq-raw\ifm_general\source\general\general_full.chunk0-bdbff8a5c6-00069.parquet`
  holds 2,013,330,256 B with SHA-256
  **`c9a407020ec03bdeedad1daca01c2b69e62babbe724295c43d9b0247ef3da992`**. It was
  re-hashed this session and equals the identity sidecar's linked-etag SHA-256
  (`sha256_independently_verified: true`).
- The scratch copy `C:\XLM-scratch\ifm_general\p02\f00000.parquet.part` has the same
  bytes and SHA-256, with state `complete`.
- The p02 run promoted the durable copy before adapting.
- `.staging/p02/f00000` still holds a 78,475,680 B partial `documents.jsonl`
  from the failed attempt. This is private p02 staging; p03 stages under
  `.staging/p03`.

## 2. The bad row class (offline scan of the retained shard)

The scan is `scan_retained_f00000.py` (`scan_f00000.json`). It took 11.7 s and
read only the `text` and `token_count` columns.

Schema: `text: string`, `token_count: int64`, both nullable. 329,409 rows in 80
row groups.

| `text` | count |
|---|---:|
| column in schema | yes (`string`) |
| null | 0 |
| non-string | 0 (not representable in a string column) |
| exact empty `""` | **2** |
| whitespace-only | 0 |
| non-empty valid | 329,407 |

| `token_count` | count |
|---|---:|
| column in schema | yes (`int64`) |
| null / wrong type / negative | 0 / 0 (not representable) / 0 |
| zero | 2 (rows 4983 and 193631) |
| min / max | 0 / 7,863 |

**Invalid rows:**

- Row **4983**: row group 1, row 842 in the group.
- Row **193631**: row group 47, row 289 in the group.

Both are exact-empty and declare `token_count` 0. That is 2 of 329,409 rows,
a fraction of 6.07e-6.

**The real failure reproduces.** Running the production record stream
(`selected_payloads` → `json.loads` → `IfmGeneralAdapter`) over rows [0, 4984)
accepts 4,983 rows. It then raises `MissingFieldError` *"adapter 'ifm_general'
requires non-empty upstream field 'text'."* at `mix01_adapters.py:67 in
_require`. The record's keys are `text` and `token_count`, and `text` is a
`str` of 0 characters. Row 193631 fails the same way.

- **The first failing source row index is 4983.**
- **The failure class is an exact-empty string** in a structurally present string
  column. It is not a missing column, a null, a wrong type or whitespace.
- It is isolated (2 rows), not widespread.

**Cross-check from footer statistics.** `footer_statistics.py` reads only the
four raw footers saved by the earlier audit. Only `token_count` minimum 0 and
the null counts are used; no text statistic value is printed.

| footer | rows | text nulls | row groups with `token_count` min 0 |
|---|---:|---:|---|
| General f00000 (00069) | 329,409 | 0 | **[1, 47]**, exactly the two empty rows |
| General f00001 (00146) | 328,568 | 0 | none, consistent with its 0-rejection seal |
| Planning f00000 (00058) | 350,701 | 0 | none |
| Planning f00001 (00295) | 346,354 | 0 | **[30, 71]** |

In General, `token_count == 0` marks exactly the empty rows. The selected
Planning p02 file `planning.chunk1-6d580bf230-00295` shows the same signature
in two row groups. Planning p02 would very likely have failed the same way.
This is inferred from statistics, not scanned.

## 3. Adapter contract audit

- `_require` (frozen `mix01_adapters.py`) raises `MissingFieldError` for an
  absent key, a null, and a blank string alike. `_ifm_text_and_token_count`
  (shared by `IfmGeneralAdapter` and `IfmPlanningAdapter`) uses it for `text`,
  then refuses a non-string.
- Production adaptation (`source_local`) records only `RecordRejectedError`
  in the ledger. Its code is the class name and its category is `policy`,
  except the Essential-Web malformed class. Every other `AdapterError` aborts
  the unit.
- Structural faults never reach the adapter as "rows". A column missing from
  the Parquet schema is refused by projection (`ProjectionRefusal` →
  `RecordLimitError`) before any row is decoded. A wrong column type cannot
  carry a row value of another type.
- Precedent: `synth_en` already separates the two classes for training
  content. It uses `_require_recordable_content_text`: a missing or non-string
  column is fatal, while a present null or blank value is a policy drop,
  observed on real rows. All fatal checks run before any drop.

**Verdict: this is case 1, a bug.** The IFM adapter used one fatal path for
two different things: a structural absence and an isolated row whose string
content is empty. The upstream data does **not** violate the certified
schema. The column exists with the certified type, no value is null, and the
declared `token_count` agrees that the document is empty (0 tokens).
Certification accepted 698/698 calibration rows only because its 698-row
group held no empty row.

## 4. Policy implemented (the smallest sound change)

`src/xlm/data/adapters/ifm_adapters.py` holds IFM row contract v2. Exactly one
branch differs from v1.

| row | v1 | v2 |
|---|---|---|
| `text` key absent | fatal `MissingFieldError` | fatal (v1 path, same message) |
| `text` null | fatal | **fatal** (null never observed: 0 in 4 footers; not justified) |
| `text` non-string | fatal | fatal |
| `token_count` present but not a non-negative int (incl. bool, str, float) | fatal | fatal; checked **before** a blank-text rejection, so a malformed row is never hidden |
| `text` a string that is empty or whitespace-only | fatal (whole file) | **`IfmEmptyTextError`** (a `RecordRejectedError`): recorded, the file continues |
| valid `text` | document | the same v1 `adapt` builds it: byte-identical, text verbatim, same doc ID |

The stable rejection code is **`IfmEmptyTextError`** (the class name, as for
every ledger code). The reason is text-free and deterministic, for example:
`adapter 'ifm_general' records a row whose upstream 'text' is empty (0
characters, declared token_count 0); it has no training content.`

Nothing is substituted, manufactured, coerced or silently skipped. Rejections
appear in the unit's ledger (`adaptation_rejections.jsonl.zst`), in
`rejection_counts_by_code`, in `rejected`, in the account and in the receipt.

**Real-data proof** (`dry_adapt.py`) replays the production serial loop in
memory under the registry adapter:

| shard | rows | documents | rejected | canonical bytes | documents SHA-256 |
|---|---:|---:|---:|---:|---|
| f00001 (sealed) | 328,568 | 328,568 | 0 | 4,842,744,134 | `df33f69f…6e1e` = **the sealed receipt, byte-identical** (5,195,802,585 B) |
| f00000 (failed) | 329,409 | 329,407 | 2 `IfmEmptyTextError` (rows 4983, 193631) | 4,842,990,792 | `c97a73c5…3d58` (5,196,855,011 B), ledger `da91d12b…4cea` |

f00000 under v2 yields 1,210,747,698 estimated tokens. Runs took 141.9 s and
139.2 s of wall time.

## 5. General and Planning

The helper and the upstream format are shared. Both views are the same
dataset revision and schema (`text: string`, `token_count: int64`), produced
by the same writer. Planning's selected p02 file shows the same `token_count
== 0` signature. The policy is therefore **explicitly shared**:
`reject_empty_ifm_text` serves both v2 adapters, and every authored case is
tested for both. **Both views' semantics change, deliberately.**

## 6. Identity: what binds adapter semantics

Before this change:

- A bridge receipt bound `adapter_code_identity()`, the SHA-256 of all of
  `mix01_adapters.py` plus `columns.py`.
- `verify_current` refuses a changed identity. `current_admission`, which
  `plan`, `authorize` and `run` call, goes through it.
- So the identity *did* bind the semantic change, but too coarsely.

That coarseness is the problem. `mix01_adapters.py` is frozen twice:

- Its bytes are in every one of the 8 admitted Mix-01 bridges.
- Essential-Web freezes it as part of its campaign adapter identity, which
  binds 123+ receipts and the calibration seal.

Editing it would have invalidated every source, so the frozen file is
**untouched**.

**The smallest generic binding:**

- `src/xlm/data/adapters/registry.py` is the Mix-01 source-pipeline registry:
  the frozen classes, with the IFM ids replaced by the v2 classes. Production
  (`source_local`), bridge certification and `xlm data adapt` now resolve
  adapters through it. The Essential-Web paths keep the frozen registry, whose
  Essential-Web entries are the same classes.
- `adapter_code_modules(adapter_id)` gives the frozen adapter module and the
  column module, plus the module of every `xlm` class in the registered
  class's hierarchy.
- `adapter_code_identity(adapter_id)` hashes those modules. A frozen class
  adds nothing; a versioned class adds its own module.
- So any future corrected contract changes only its own adapter's identity,
  and it cannot hide behind the old one.

Measured on the real store (`admission-gates-before.json` → `-after.json`):

| source | admission identity | gate |
|---|---|---|
| finepdfs, finewiki, synth, wiki_rewrite, simple_stories, ultrax | **identical** (bridge digests `283df718…`, `f36584a5…`, `c749c999…`, `c6eb1f46…`, `66f0c419…`, `b874f47e…`) | admitted, unchanged |
| ifm_general, ifm_planning | refuses | "adapter code changed since the evidence was bridged" |

| identity | General old → new | Planning old → new |
|---|---|---|
| bridge receipt digest | `1330da81815c0fdeccac36f95e88523787a16ed1f662fa210fe8eab0206e7045` → **`93441dfbd0ab143eec24a8dbee03caa427dca4ed4156a6b6bcf51349c3c9a7ea`** | `fb1a2b96c6635667741d945a5be6d41462375274775cf5da09441ab2c009e65a` → **`7925617b10c6482441a5616150538b932000ca7c2935260423362e1746f10116`** |
| probe fingerprint | `cca8d4cb…f913b`, unchanged (schema, files and revision are unchanged) | `1284634e…bf35`, unchanged |
| adapter code identity | mix01_adapters + columns → mix01_adapters + columns + `ifm_adapters` | same |
| calibration certification | 698/698 accepted | 1903/1903 accepted |

The new digests come from `evidence show` (`evidence-after.txt`). Nothing was
published. Because each admission decision binds the bridge digest, **both IFM
views need operator re-review and re-admission.** After that, every General
or Planning plan made under the old admission refuses to authorize or run
("admission changed since this plan was made").

## 7. Continuation: admission repair (General p03)

The existing mechanisms do not fit:

- **Limit repair** needs a changed `UNIT_LIMIT_KEYS` entry. The cause here is
  not a limit, and no limit change was faked.
- **Top-up** is refused over an INCOMPLETE plan.
- **Supersession** applies only to never-authorized, never-run plans.

**Generic addition.** `build_repair_plan` now also accepts a **changed
admission** as the repair cause:

- It records `repair.changed_admission` (old → new). The key is present only
  then, so every existing limit repair keeps its digest.
- Identical limits *and* identical admission are still refused ("resume the
  plan instead").
- `plan-repair` prints the changed admission.
- The first-pass seal's `repair_of` repeats `changed_admission`, so the seal
  shows which units were sealed under which admission.
- p02's sealed f00001 needs no re-adaptation. v2 reproduces its documents
  byte for byte (§4), and v1 could only have sealed a unit with no blank row.

**Rehearsal** (`rehearse_continuations.py`, `rehearsal.json`). It runs in memory
and stores nothing. It assumes the operator re-admits the new bridge with the
same `decision_contract` and `benchmark_risk`:

| | value |
|---|---|
| General p03 digest (rehearsal) | **`626b3356af422a81cadebac797d75e554c4491ede4a26130ca55c8b0030611dd`** |
| repairs | p02 `6c0dfe5a…9939`, sealed ranks [1] kept in p02 |
| ranks / files | [0] / `general_full.chunk0-bdbff8a5c6-00069.parquet` only |
| next_cursor | 2 (unchanged) |
| changed_admission | `bridge_receipt_digest` `1330da81…7045` → `93441dfb…a7ea` |
| retained SHA-256 (in the plan hash) | `c9a40702…a992` |
| expected transfer / requests | **0 B / 0** |
| resume-check | local_processing_retry 1; everything else 0; known and worst-case network **0 B** |

General p03 also has derived per-unit limit changes, which are not its cause.
The selected-size anchor now follows the only selected file, f00000:

| limit | p02 → p03 |
|---|---|
| max_file_bytes | 2,015,363,072 → 2,014,314,496 |
| decoded | 8,061,452,288 → 8,057,257,984 |
| durable | 10,065,245,718 → 10,059,864,622 |
| growth output | 8,049,882,646 → 8,045,550,126 |
| file deadline | 6,406.7 s → 6,403.3 s |

All of them fit f00000's measured needs: 2,013,330,256 B file, 4.85 GB decoded,
5,196,855,011 B documents, 329,409 rows within 444,703, and 4.84 GB canonical
within 6.54 GB.

**The real p03 digest exists only after renewal.** It equals the rehearsal
digest if the operator records the same decision fields.

## 8. Sufficiency and seal safety

- **Today:** General is INCOMPLETE. Sealed rank 1 contributes 4,842,744,134 B.
  Unresolved rank 0 blocks the seal, although acquired bytes exceed
  660,000,000.
- **After p03** (projected): rank 0 resolves under p03 with 4,842,990,792 B.
  The total is 9,685,734,926 B, each rank counted once, and status becomes
  SUFFICIENT.
- **Seal:** units (p02, 1) and (p03, 0). `next_cursor` stays 2. This is proven
  end to end on the authored fixture (§10).

## 9. Planning p02 (unauthorized) impact

Planning p02 `72778e8c…2141` binds bridge `fb1a2b96…e65a`. That identity is now
stale:

- Today, `authorize --plan 2` refuses: the admission check fails on the
  changed code identity.
- After renewal it still refuses with "admission changed".

Planning p02 must **not** be authorized. After renewal,
`plan-supersede --plan 2` writes Planning p03 through the existing mechanism:
changed sections `["inputs"]`, no limit changes, the same files (ranks 0 and
1), cursor 2. **The rehearsal digest is
`8661134201ca7bb04603df7cb753e7d6a76d3b07b78d20610e83f6688e3dfaa1`**, under the
same assumption as above. Chained supersession (p01 → p02 → p03) is tested.

## 10. Tests and checks

New `tests/test_ifm_empty_text_recovery.py` has 12 tests, which are 46
parametrized cases. All use authored fixtures; no corpus text.

- **Row contract** (both views):
  - Valid rows are byte-identical to v1, with verbatim text, deterministic doc
    IDs and doc IDs bound to the row.
  - `""`, `" "` and Unicode whitespace give `IfmEmptyTextError`; v1 gives
    `MissingFieldError`.
  - Missing key, null, int, list and bytes stay fatal with the v1 message.
  - `token_count` -1, True, "3" and 1.5 stay fatal, including on empty text,
    where the fatal check runs first.
  - The ledger line is deterministic and text-free, with code
    `IfmEmptyTextError` and category `policy`.
- **Identity:**
  - The registry overrides only the IFM ids, and production resolves through
    it.
  - Every non-IFM `adapter_code_identity` equals the pre-change identity; IFM
    gains exactly `ifm_adapters`.
  - A bridge certified under v1 code is refused for IFM, while a non-IFM one
    passes the code check.
  - The driver refuses a plan under a stale admission.
- **Planner:**
  - An admission repair under unchanged limits is accepted, distinct (no
    `start_rank`, no `supersedes`) and deterministic.
  - Unchanged limits and admission are refused.
  - A limit repair carries no `changed_admission`.
  - The Planning p02 supersession after renewal changes only `inputs`; p01 and
    p02 stay byte-identical, both refuse `authorize`, and resolution is
    `{1: [], 2: [], 3: [0, 1]}`.
- **End to end** (authored IFM Parquet behind the loopback server):
  - p1 runs under the frozen v1 adapter. Rank 0 seals; rank 1, with one empty
    row, fails with `MissingFieldError`.
  - The pass is INCOMPLETE, with sealed bytes already ≥ the requirement, and
    the seal refuses.
  - A repair under unchanged admission is refused. The admission repair p2
    covers rank 1 only, keeps cursor 2, has 0 expected transfer and is
    classified local_processing_retry.
  - p1 refuses to run, and p2 refuses until it is authorized.
  - The offline run makes 0 HTTP hits and transfers 0 B. It gives 299
    documents, 1 `IfmEmptyTextError`, and documents equal to v1's for every
    non-empty row.
  - p1's bytes are unchanged and both plans verify.
  - The result is SUFFICIENT, with repairs `[{2, 1, [1]}]` and cursor 2.
  - The seal covers units (1,0) and (2,1) once each. `repair_of` carries
    `changed_admission`, and the seal is write-once.
  - The fixture mirrors the real ranks (the failing file is rank 1), because
    inline processing seals in rank order.

Changed tests: `test_certified_evidence.py` (the identity stub takes an adapter
id) and `test_source_rowgroups.py` (state-free check over the registry, so the
v2 IFM `adapt` is covered).

Commands run with thread variables set to 1, `TOKENIZERS_PARALLELISM=false`,
and `--basetemp` under `C:/XLM-scratch/pt-ifme`.

| command | result |
|---|---|
| `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_ifm_empty_text_recovery.py -n 0` | first run: 45 passed, 1 failed (the authored file was too small for the receipt metadata bound; fixture enlarged). **Final: 46 passed, exit 0** |
| same runner, 34 related files, `-n 8 --dist=worksteal --max-worker-restart=0 -m "not serial"` | **619 passed, 4 failed**, exit 1; see below |
| `… tests/test_source_repair.py tests/test_source_run.py tests/test_ifm_empty_text_recovery.py tests/test_ifm_production_bounds.py -n 8 …` | 88 passed, exit 0 |
| `ruff format --check` (17 files), `ruff check` | exit 0, exit 0 |
| `mypy --strict` on ifm_adapters, registry, certified_evidence, source_local, source_plan, source_run, data_cmd, scripts/mix01_source | Success, 8 files, exit 0 |
| `git diff --check` | exit 2, only for `src/xlm/cli/data_cmd.py:1978` |
| `git -c core.whitespace=cr-at-eol diff --check` | exit 0 |

About the `git diff --check` result: `data_cmd.py` is committed with CRLF
(`i/crlf`), so every added line carries a CR. Its last three commits show the
same flags (16, 34 and 4). The file stays uniformly CRLF.

The 34 related files:

- Repair, plan, run and growth: ifm_empty_text_recovery, ifm_production_bounds,
  source_repair, source_plan, source_run, source_rowgroups, source_file_bounds,
  source_growth, source_growth_integration, source_record_bound,
  source_discovery, source_archive.
- Evidence, admission, CLI and inventory: certified_evidence,
  mix01_source_cli, mix01_admission, source_admission, mix01_views,
  ifm_requirement_split, mix01_inventory, source_doc_ids, production_ingest.
- Adapters: synth_adapter_semantics, finepdfs_live_schema, ultrax_ultrafineweb.
- The six `*_live_certification` files.
- Essential-Web: essential_web_bulk, essential_web_fast,
  essential_web_malformed_whole_pass, essential_web_pool_seal.

**All 4 failures are pre-existing.** All 4 also fail on an unmodified checkout
of base `2c0f91a` (a temporary worktree, since removed).

- `test_essential_web_malformed_whole_pass::test_committed_malformed_amendment_binds_the_running_code`
  fails deterministically. The committed Essential-Web compatibility record no
  longer matches the running hashes of `essential_web_recovery.py`,
  `essential_web_local.py` and `source_parquet.py`. This session touched none
  of them. **The Essential-Web chain needs its own follow-up.**
- `test_essential_web_fast::test_operator_drivers_parse_…` and
  `test_essential_web_bulk::test_operator_driver_parses_…` fail
  deterministically in their PowerShell subprocess (`stderr` is `None`, plus a
  UnicodeDecodeError in a reader thread). This is environmental.
- `test_source_repair::test_failed_unit_is_repaired_offline_and_sealed_once`
  is **intermittent**. It failed once on the unmodified base, then passed 8
  base runs (6 alone, 2 in combinations). On this branch it passed alone and in
  the 88-test run, and failed once in the 34-file run. Its cause was not
  captured, and it was not rerun until green.

**Not run:** the full suite and its serial selection, live, network and CUDA
tests, and any production run.

## 11. Requirement ledger

| task | status |
|---|---|
| 1 snapshot history; rank 1 sealed once, rank 0 unresolved; f00000 re-hashed, local, complete | VERIFIED |
| 2 offline bad-row scan; failure reproduced; first failing row 4983 | VERIFIED (real shard, offline) |
| 3 contract audit (fatal versus reject) | IMPLEMENTED (§3) |
| 4 bug or drift: a bug, the adapter conflates the two | VERIFIED (real shard plus footers) |
| 5 explicit `IfmEmptyTextError` policy; accepted rows byte-identical | IMPLEMENTED, VERIFIED (fixture, and real f00001 hash) |
| 6 shared General and Planning policy | IMPLEMENTED, VERIFIED (both views tested). Planning evidence is footer statistics only |
| 7 per-adapter identity; only IFM invalidated | IMPLEMENTED, VERIFIED (real store gates) |
| 8 recertification tests; `evidence show` | VERIFIED; no publish or admission (NOT RUN by design) |
| 9 admission-repair continuation | IMPLEMENTED, VERIFIED (fixture), REHEARSED (real, in memory). The real p03 is BLOCKED on operator re-admission |
| 10 sufficiency and seal safety | VERIFIED (real INCOMPLETE; fixture SUFFICIENT and seal) |
| 11 Planning p02 stale, supersede after renewal | IMPLEMENTED (existing mechanism), VERIFIED (fixture), REHEARSED |
| 12 tests and checks | focused and related run (§10); full suite NOT RUN |
| 13 docs | IMPLEMENTED (this report, STATUS, runbook) |
| 14 commit, no push | see STATUS |

## 12. Open limitations

- Null `text` stays fatal. It is not observed, and the schema allows it; a
  future null row stops the unit for an operator decision.
- Planning's empty rows are inferred from `token_count` statistics. Its shard
  was not scanned (no local copy, and no download was authorized).
- Overshoot is unchanged: about 9.69 GB of canonical text against 660 MB
  needed (§11 of the bound-recovery report). This is the operator's decision.
- f00000's 2,013,330,256 B were transferred during p02. Because the unit
  failed, they appear in no unit receipt. The machine-level counter recorded
  4,191,667,539 B received. p03 transfers 0 B.
- The p02 private staging residue (78,475,680 B) stays until the operator
  cleans it. No run removes another plan's staging.

## 13. Operator commands (STOP points marked)

Run from `F:\Project\xlm-ifm-bounds`, or after merging this branch. The base
code has no v2 adapter and no admission repair.

```powershell
$env:XLM_DATA_ROOT='G:\XLM'; $env:XLM_HOME='G:\XLM\xlm-home'; $env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'; $env:PYTHONUTF8='1'
$X = 'uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py'

# STOP 0: review this commit and the new bridge digests before any store write.
Invoke-Expression "$X evidence show --source-key ifm_general"    # expect 93441dfb...a7ea
Invoke-Expression "$X evidence show --source-key ifm_planning"   # expect 7925617b...0116

# 1. Renew IFM General evidence and admission (operator decisions; review dirs are write-once, so use fresh ones)
Invoke-Expression "$X evidence publish --source-key ifm_general"
Invoke-Expression "$X evidence verify --source-key ifm_general"
Invoke-Expression "$X review show --source-key ifm_general"
Invoke-Expression "$X review record --source-key ifm_general --review-dir G:\XLM\reviews\ifm_general-r2 --operator '<operator>' --license-decision approve_research_pretraining --provenance-decision approved --benchmark-risk suspect_with_mitigation --rationale '<rationale>'"
Invoke-Expression "$X admit --source-key ifm_general --review-dir G:\XLM\reviews\ifm_general-r2"

# 2. General p03 admission repair (offline, zero network)
Invoke-Expression "$X plan-repair --source-key ifm_general --plan 2"   # expect PLAN DIGEST 626b3356...11dd with the same decisions
Invoke-Expression "$X resume-check --source-key ifm_general --plan 3"  # expect local_processing_retry 1, 0 network bytes
# STOP: review the p03 digest
Invoke-Expression "$X authorize --source-key ifm_general --plan 3 --digest <p03-digest> --operator '<operator>'"
Invoke-Expression "$X run --source-key ifm_general --plan 3 --offline"
Invoke-Expression "$X verify --source-key ifm_general --plan 3"
Invoke-Expression "$X sufficiency --source-key ifm_general"            # expect SUFFICIENT, next_cursor 2
Invoke-Expression "$X seal --source-key ifm_general"

# 3. Planning: renew, then supersede stale p02 (never authorize p02)
Invoke-Expression "$X evidence publish --source-key ifm_planning"
Invoke-Expression "$X evidence verify --source-key ifm_planning"
Invoke-Expression "$X review record --source-key ifm_planning --review-dir G:\XLM\reviews\ifm_planning-r2 --operator '<operator>' --license-decision approve_research_pretraining --provenance-decision approved --benchmark-risk suspect_with_mitigation --rationale '<rationale>'"
Invoke-Expression "$X admit --source-key ifm_planning --review-dir G:\XLM\reviews\ifm_planning-r2"
Invoke-Expression "$X plan-supersede --source-key ifm_planning --plan 2"   # expect PLAN DIGEST 86611342...faa1
# STOP: review the Planning p03 digest; authorizing and running it uses the network (about 4.0 GB).
```
