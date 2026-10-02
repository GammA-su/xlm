# Global C05 preparation

Current state: **blocked preparation, not an executable C05 plan**. See the
[audit and requirement ledger](../implementation/reports/C05-GLOBAL-CONTAMINATION-PLAN.md).
The source input manifest is not an exclusion receipt or permission to train.

Run from `F:\Project\xlm-c05-global`. The existing environment has Python 3.12.13
and the locked CPU/eval dependencies. No installation or network is needed:

```powershell
$env:UV_PROJECT_ENVIRONMENT='F:/Project/xlm-common-pile/.venv'
$env:PYTHONPATH='F:/Project/xlm-c05-global/src'
$env:UV_OFFLINE='1'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'

uv run --offline --locked --no-sync python -m xlm.data.exclusion.preparation verify --data-root G:/XLM --scratch-root C:/XLM-scratch --manifest docs/implementation/evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json

uv run --offline --locked --no-sync python -m xlm.data.exclusion.preparation preflight --data-root G:/XLM --scratch-root C:/XLM-scratch --manifest docs/implementation/evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json
```

`verify` expects exit 0: metadata and sizes reproduce. It does not reread corpus
contents. `preflight` expects exit **2**: required production facilities/material
evidence are missing. Do not interpret that exit as authorization or bypass it.
An altered seal/receipt/plan/file size or malformed pin exits 1. The first-pass
manifest is write-once; a changed corpus requires a new reviewed lineage.

To recreate the manifest at a fresh output path outside the data store, use the
same arguments with `inventory` and a new `--manifest` path. The command inventories
all ten fixed baseline sources and fails on any missing component. It cannot add
txt360, narrow to a convenient subset or run C05. No `authorize`, `run`, `resume`
or post-run verification verb exists yet.

## Benchmark preparation boundary

The operator must first inventory local benchmark artifacts under the isolated
identity/machine required by `EVALUATION_POLICY.md`. Return only content-free
metadata and trusted receipts: exact repository/revision/config/split/count/file
hashes, complete split coverage, render/normalization/signature identities,
code/dependency identity and isolation attestation. Do not send labels, example
text, raw signatures or detailed matches to the agent.

If material discovery needs network, the following **future operator-only** commands
list metadata at the four existing immutable pins. They were NOT EXECUTED. They
do not fetch benchmark payloads and do not create a usable benchmark index.
The live lister allows only `https://huggingface.co/api/datasets/.../tree/...`;
payload/resolve endpoints are refused. Scope is these four exact repositories and
revisions. Each call: 16 pages, 4,096 items, 32 requests, 8 MiB metadata, 2 retries,
15-second request timeout, 300-second deadline. Aggregate maxima: 128 requests,
32 MiB metadata and 1,200 seconds. Keep a separate bounded metadata output directory;
no corpus cache or extraction is involved. A refused/incomplete listing stops the
workflow; never widen bounds or switch revisions automatically.

Run only after the operator separately authorizes this metadata access, from an
operator-controlled checkout/environment. `UV_OFFLINE=1` and uv's `--offline` keep
dependency resolution offline; the explicit HF transition permits only the lister's
bounded network code. Restore both HF flags even on error:

```powershell
$env:HF_HUB_OFFLINE='0'
$env:HF_DATASETS_OFFLINE='0'
try {
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source arc_easy --view ARC-Easy --repo allenai/ai2_arc --revision 210d026faf9955653af8916fad021475a3f00453 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/arc_easy.json
    if ($LASTEXITCODE -ne 0) { throw 'ARC metadata listing failed' }
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source blimp --view all --repo nyu-mll/blimp --revision 877fba0801ffb7cbd8c39c1ff314a46f053f6036 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/blimp.json
    if ($LASTEXITCODE -ne 0) { throw 'BLiMP metadata listing failed' }
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source hellaswag --view all --repo Rowan/hellaswag --revision 218ec52e09a7e7462a5400043bb9a69a41d06b76 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/hellaswag.json
    if ($LASTEXITCODE -ne 0) { throw 'HellaSwag metadata listing failed' }
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source piqa --view all --repo baber/piqa --revision 142f6d7367fd9877f0fb3b5734ea6a545f54cdd1 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/piqa.json
    if ($LASTEXITCODE -ne 0) { throw 'PIQA metadata listing failed' }
} finally {
    $env:HF_HUB_OFFLINE='1'
    $env:HF_DATASETS_OFFLINE='1'
}
```

No filters are specified: discover the repository files without assuming format or
silently missing splits. `view` labels above are inventory labels, not verified
publisher dataset configurations. Follow-up protected payload acquisition needs an
exact reviewed file list, host/redirect allowlist, rights decision, byte/cache/
scratch/output limits and an actual supported operator implementation. That
executable acquisition command is not available at this stop point.

## Before a future execution plan

Complete the streaming matcher, known-lineage propagation, deterministic per-file
journal/resume, bounded disk/RAM/deadline enforcement and protected atomic output
publication in the existing C05 implementation. Bind the verified benchmark inventory
and all policy/code identities. Demonstrate short-item handling, repeated leakage,
option permutations, false-positive controls, interruption and crash recovery on
authored fixtures. Integrate protected kept-membership verification into tokenizer
fit and final component selection. The current train-split check is insufficient.

Then generate a concrete plan and resource envelope for operator review, with a
new execution digest and exact authorization/run/post-run commands. Until then,
do not run C05 globally or use first-pass data for tokenizer/model training.
