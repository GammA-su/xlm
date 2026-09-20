# P23 D07 — after-evidence

Branch `fix/d07`, isolated worktree `D:\Project\xlm-d07`, own venv and
`XLM_HOME`. Offline throughout: no downloads beyond the authorized tooling
sync, no GPU, no research-sized work.

| File | What it is |
|---|---|
| `inventory_report.py` / `inventory_after.json` / `inventory_run.log` | Tensor inventory at each level, measured from the container header |
| `partition_summary.json` | Full-suite partitioned run: 84/84 files, per-partition exit codes and timings |
| `failures_observed.txt` | The 13 observed failures, attributed in the report §9 |

## Inventory, reported at each level separately

| Level | Measure | Value |
|---|---|---|
| Model | Logical state names | 21 |
| Model | Unique deployed parameters | 98,880 |
| Model | Total instantiated incl. aliases | 115,520 |
| Model | Tied parameters | 16,640 |
| Serialized | Tensor entries | 20 |
| Serialized | Elements | 98,880 |
| Serialized | Payload bytes | 395,520 |
| Container | Header/metadata bytes | 1,912 |
| Container | File bytes | 397,432 |

Cross-checks, all true:

* `serialized_payload_bytes == unique_deployed x 4`
* `serialized_elements == unique_deployed`
* the alias name carries no payload
* `file_bytes == container_header_bytes + serialized_payload_bytes`

Behaviour: logit difference `0.0`, Parameter identity restored, deployed
parameter count identical, tokenizer fingerprint identical.

Before-state for comparison is in `../before/`, preserved unchanged at commit
`e30cd3b`: 21 entries, 462,080 payload bytes, 66,560 duplicated.
