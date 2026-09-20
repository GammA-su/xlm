# First bounded acquisition pilot (operator execution)

D02 implementation verification is still IN PROGRESS. These are the implemented
CLI paths, not evidence that any real source has been acquired or admitted.
Real discovery, acquisition and preparation are operator actions. Production
platform acceptance remains BLOCKED; D04/D05, D08, D07 and deferred D03 work have
their own gates. Use an isolated `XLM_HOME` and the pinned CPU environment.

```powershell
$env:XLM_HOME = 'D:\Project\xlm-operator-pilot'
uv sync --offline --locked --extra cpu --extra eval
uv run --offline --locked --extra cpu --extra eval xlm data sources --json
```

`uv --offline` prevents dependency downloads. It does **not** disable an explicitly
invoked `xlm data probe --live` or `data fetch`. Those commands below are for you
to run after source review. If the locked environment is not cached, arrange its
installation separately; this remediation did not authorize network installation.

## Select and discover one view

Review `manifests/datasets.catalog.yaml` and the source's actual publisher view.
Set `$source`, `$view` and `$catalog` to the chosen catalog entry/view and reviewed
catalog path. Do not use an invented revision or assume a sample schema is valid.

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data probe --source $source --view $view --catalog $catalog --live --probe-id first-reviewed-probe --budget-mib 16 --json
uv run --offline --locked --extra cpu --extra eval xlm data audit --catalog $catalog --json
uv run --offline --locked --extra cpu --extra eval xlm data mix01-status --catalog $catalog --json
```

Discovery uses the existing host allowlist, 20 requests, 10-second request timeout
and a cumulative 60-second deadline. Its journal is under
`$XLM_HOME/discovery/SOURCE_VIEW_PROBEID.json`; repeating that identity retains its
spent allowances and deadline. `--probe-id` explicitly names a different reviewed
discovery attempt. This is not a way to extend an existing plan's allowance.
Probe publication uses the existing immutable source/view artifact identity: a
new, different observation cannot overwrite it. Use `--no-publish --json` to inspect
another observation, retain that report, and resolve any evidence conflict
explicitly. Do not delete or repair an old artifact to force publication.

The discovery output records the resolved revision, inventory, schema (when
actually established), fingerprint and unresolved requirements. A metadata-only
or partial result is not corpus evidence. Hub tree inventory may be partial;
HTTPS-manifest discovery currently establishes metadata, not row-schema coverage.
Provider/CDN allowlist incompatibilities fail closed. Do not enable remote code,
accept terms automatically or add hosts just to bypass a refusal.

Before **source admission**, require the actual immutable revision; the exact
view schema and tested adapter ID/version; license/provenance/benchmark-risk
review; a decision bound to the observed fingerprint and revision; explicit
operator approval; and the applicable real-source pilot evidence. The existing
`data admit --help` documents its decision flags. A successful transport receipt
does not meet these gates. Synthetic tests cannot supply missing admission proof.

## Review and approve the immutable acquisition plan

Obtain the original relative filename(s) from actual discovery. `$files` is a
comma-separated selection, not a glob. For selected records, author `rows.json`
as a mapping from each exact filename to `[start, stop]`, zero-based, half-open.
The JSONL index counts nonblank object records; the Parquet index counts rows.
Selection is explicit and generally biased; it is not a representative sample
merely because the CLI also records a seed.

Create `limits.json` with all reviewed bounds, for example a **ceiling**, not a
claim that a given Parquet shard fits:

```json
{
  "max_transferred_bytes": 16777216,
  "max_decompressed_bytes": 33554432,
  "max_records": 100,
  "max_temp_disk_bytes": 67108864,
  "max_output_disk_bytes": 67108864,
  "max_requests": 20,
  "max_retries": 1,
  "max_workers": 1,
  "per_request_timeout_seconds": 10,
  "overall_deadline_seconds": 600,
  "max_decompression_ratio": 15,
  "max_record_bytes": 1048576,
  "max_parser_bytes": 33554432,
  "max_scanned_records": 1000
}
```

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data plan --source $source --view $view --catalog $catalog --files $files --mode selected_records --row-ranges rows.json --limits limits.json --output pilot-plan.json
```

Review the entire file, behavioral hash, pinned revision, requested selection,
sampling limitations, resource ceilings and output identity. If an independently
verified publisher SHA-256 exists, supply its filename-to-digest JSON through
`--expected-digests`; do not manufacture one. Whole-file mode verifies those
digests. A selected-row artifact cannot verify the hash of untransferred bytes.

JSONL selection scans only the required prefix with bounded lookahead. Supported
Parquet selection needs metadata/footer and intersecting row-group ranges; those
requests, compressed bytes and decoded groups all consume allowance. The server
must support exact ranges and a stable strong ETag. Ignored ranges, oversized
groups, unavailable budgets and unsupported selected formats (including gzip)
are explicit refusals, with no whole-shard fallback. For whole originals use
`--mode whole_file` and omit `--row-ranges`; exceeding a limit refuses publication,
never truncates the original under its original identity.

## Fetch, interrupt, resume and verify

Your explicit approval is bound to the plan you reviewed:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data fetch --plan pilot-plan.json --pilot-approved
uv run --offline --locked --extra cpu --extra eval xlm data status --plan pilot-plan.json --json
```

Default paths are `$XLM_HOME/acquisition/PLAN_ID/raw` and `.../scratch`. You can
specify `--output-dir` and `--scratch-dir` on fetch; resume must use the same roots.
Ctrl+C interrupts the attempt. Rerun the same fetch command to resume within its
original deadline and remaining allowance. Whole-file resume checks the committed
prefix; selected-record retry constructs a new complete private selection while
retaining earlier spent allowances and interrupted private staging. Unknown
in-flight transfer reservations stay unavailable after a crash. Never delete the
journal to replenish capacity. A completed corrupt/incomplete original is refused,
not overwritten or repaired.

Set `$raw` to the actual raw directory printed/derived from the plan:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data verify --plan pilot-plan.json --output-dir $raw --json
```

For custom scratch roots add `--scratch-dir` to status and verify. Verification
requires the original completed journal, checks actual bytes and locators, and
publishes through D01. `--no-publish` checks without publishing. Publication needs
room for the raw original plus artifact copy, private staging, receipt and bounded
manifest overhead (8 MiB reserved conservatively); these are included in the
plan's storage allowances. Identical publication requests reuse a verified artifact.

Confirm `records_acquired > 0` in status and positive `files[].record_count` in
the receipt. Opaque objects have unknown record counts; downloaded metadata is
not corpus acquisition. Inspect the actual selected JSONL objects and their
`_xlm_acquisition`: source/repository/revision/file, row index, JSONL byte offset
and length or Parquet row group/index, stable ETag and selection hash. Parquet
record hashes cover canonical JSON serialization, not compressed original bytes.

Artifacts and receipts appear under `$XLM_HOME/raw_dataset/ARTIFACT_ID`, with the
existing ledger and completion marker. Progress/error/accounting is in
`scratch/journals/PLAN_ID.progress.json`. It distinguishes cumulative consumed
response-body bytes/requests/decompression from occupancy and pending reservations.
These are application body bytes, not TCP/TLS wire traffic or provider billing.

## Larger selection and preparation

Create a **new reviewed plan file** with new ranges and explicit bounds, preserving
the old plan, journal and artifacts. Generated plan/output identities change with
the request; the old account is neither cleared nor extended. Account for both
retained jobs in your physical storage budget. Crossing C13 pilot bounds requires
verified production admission and plan-bound authorization; the public production
admission binding remains unavailable and refuses execution. An admission-reference
string is not evidence. Do not use repeated tiny plans to impersonate production
authorization.

For preparation, author a config using the existing `recipes/prepare` schema and
the exact verified raw path and tested adapter. Declare every stage, approval,
output and watched input. Declare aggregate `fetch_max_bytes`,
`max_subprocess_output_bytes`, `max_temp_disk_bytes`, `max_output_disk_bytes`,
`max_decompressed_bytes`, `max_record_bytes`, `max_records`, `max_attempts`,
`max_network_requests` and `overall_deadline_seconds` in `budgets`.

```powershell
uv run --offline --locked --extra cpu --extra eval xlm prepare --config operator-prepare.yaml --plan-only --json
uv run --offline --locked --extra cpu --extra eval xlm prepare --config operator-prepare.yaml --authorize --json
```

Use an isolated home/output root. The config, external copy/watched inputs, code
and lock bind `.accounting/resources.json`; changes require a new reviewed job,
not an accounting reset. Check-only stages and `--force` consume allowance too.
Nested fetch reserves its declared limits before launch. Child stdout/stderr is
spooled under `.accounting/logs`; `prepare_state.json` records stage outcomes.
Record inspection counts work across stages, not unique documents. Child disk
usage is sampled and checked before successful completion, not enforced by an OS
quota; parser/managed-copy/log bounds are explicit. There is no hard OS memory
limit. Review real-source adapter limits before a larger preparation job.

After real admission and verified preparation, use the separate
[D03 baseline-pilot runbook](D03_BASELINE_PILOT.md). Benchmark-based model selection
and full research-platform acceptance remain separate, unresolved gates.
