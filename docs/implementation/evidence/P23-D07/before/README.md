# P23 D07 — preserved before-evidence

Captured 2026-09-20 in the isolated worktree `D:\Project\xlm-d07`, branch
`fix/d07` at starting commit `bd01e608769e519b23f123ead56772ad038482fe`.
Own venv, own `XLM_HOME`, own work dirs. Offline; no GPU, no downloads.

| File | What it is |
|---|---|
| `repro_d07.py` | Bounded adversarial reproduction through the real export path |
| `repro_results.json` | Observed before-state |
| `repro_run.log` | Full stdout/stderr |

## Method

`xlm.export.writer.export_model` is called on a tiny authored 2-layer model.
The resulting `model.safetensors` is then inspected by parsing the **real
container header** (`u64` header length + JSON with per-tensor `data_offsets`),
so payload bytes are measured per tensor rather than inferred from file size.
Container/metadata overhead is reported separately. Nothing is mocked.

## Verified environment

```text
xlm    -> D:\Project\xlm-d07\src\xlm\__init__.py
torch  -> 2.14.0+cpu
safetensors -> 0.8.0
python -> 3.12.13, D:\Project\xlm-d07\.venv\Scripts\python.exe
```

```powershell
$env:PYTHONPATH = ''
$env:UV_PROJECT_ENVIRONMENT = 'D:\Project\xlm-d07\.venv'
$env:XLM_HOME = 'D:\Project\xlm-d07\.d07\home'
.\.venv\Scripts\python.exe `
    docs/implementation/evidence/P23-D07/before/repro_d07.py .d07/work/repro   # exit 0
```

## Control A — genuine tie: DEFECT CONFIRMED

The fixture is genuinely tied (`model.lm_head.weight is model.embed_tokens.weight`).

| Measure | Value |
|---|---|
| Logical state-dict names | 21 |
| Unique deployed parameters | 98,880 |
| Total instantiated (counting aliases) | 115,520 |
| Tied parameters | 16,640 |
| Model-reported tied aliases | `lm_head.weight -> embed_tokens.weight` |
| Manifest `parameters_deployed` | 98,880 (correct) |
| Serialized tensor entries | 21 |
| **Distinct payload byte ranges** | **21** |
| `embed_tokens.weight` byte range | `[0, 66560)` |
| `lm_head.weight` byte range | `[395264, 461824)` |
| Serialized payload bytes | 462,080 |
| Payload if stored single-copy | 395,520 |
| **Duplicated payload bytes** | **66,560 (16.8% of deployed payload)** |
| Header/metadata bytes | 2,000 |
| File bytes | 464,080 |

The two aliases occupy **disjoint byte ranges**: the tied payload is written
twice. The manifest already records `tied_mapping = {"tied_lm_head":
["embed_tokens.weight", "lm_head.weight"]}` and the correct deployed count, so
the accounting is honest while the *storage* violates C08's "tied storage is
saved and counted once".

Root cause is explicit in the source — `writer._expand_shared_tensors`:

```python
# "Clone storage-shared tensors so safetensors accepts the bundle."
if pointer in seen:
    expanded[key] = value.clone()
```

## Control B — equal-valued independent parameters: correct today

With `tie_embeddings=False` and `lm_head.weight` copied to equal values, the two
Parameters remain independent: the model reports `tied_parameters = 0`, no
`tied_aliases`, the manifest carries an empty `tied_mapping`, and the two
tensors occupy separate payload ranges. **Any repair must preserve this** — these
are separately trainable and must never be deduplicated because their values or
hashes happen to match.

## Control C — numerical behavior: already correct

| Measure | Value |
|---|---|
| Max absolute logit difference, original vs reloaded | **0.0** |
| Parameter identity restored on load | True |
| Tokenizer fingerprint preserved | True |

The bundle reloads exactly and restores tied Parameter identity. This confirms
D07 is a **storage** defect, not a model-definition or numerical defect, and it
is why fresh-process parity alone never established the single-copy requirement.
