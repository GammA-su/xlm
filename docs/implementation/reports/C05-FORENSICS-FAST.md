# C05 component forensics: fast path, live progress (2026-10-05)

Branch `perf/c05-forensics-parallel-progress`, from `858eb9e`. Authored fixtures and
bounded benchmarks only. No G:, X:, real proof, real ledger, real corpus or protected
material was read. The real forensic command has not been run.

## Result

| | |
|---|---|
| report identity | **identical to the 858eb9e tool (kept as `scripts/c05_component_forensics_reference.py`, the oracle)** on every field except the intentionally corrected `corpus_attribution` breakdown and the new `ledger_matches_completion`; byte-identical at workers 1/2/4/8/16 |
| authored benchmark, 895,136 ledger rows | reference **39.0 s**; fast **10.85 s at 8 workers (3.6x)**; 11.0 s at 4; 11.5 s at 16; 19.8 s at 1 |
| projected real run, 8 workers | **about 1.5-2.5 min** (reference: about 9-11 min) |
| recommended `--workers` | **8** (the default) |
| progress | `[FORENSICS]` stages on stderr (`text`/`jsonl`, `--progress-interval`, `--no-progress`); stdout is only the report |

## Original architecture (858eb9e) and its bottlenecks

Stages:

1. ledger pass 1: per-allocation decisions and excluded family sizes;
2. ledger pass 2: target members and focus rows;
3. ledger pass 3 (`--read-corpus`): every row of every allocation present in the
   target;
4. every fact unit;
5. single-threaded corpus scan of all those allocations' files;
6. the protected index;
7. Python union-find over the target graph, rebuilt from scratch for each stage,
   each class alone, the seed stage, the duplicate-only stage and each removal probe.

cProfile on the authored fixture (75.9 s under the profiler, 38.8 s plain):

| component | share |
|---|---|
| strict JSON parse of 3 ledger passes + corpus rows (3.15M `json.loads`) | about 34 % |
| re-serializing the allocation key of every ledger row (`canonical_bytes`, 2.69M calls) | about 20 % |
| `main` self time: per-row Python loops (ledger, keys, unions) | about 20 % |
| memmap slicing per key, URL parsing, union-find | the rest |

At production scale this is 45M ledger parses, plus single-threaded parsing of all
SYNTH **and** all UltraX rows (about 21 GB). That is the 10-20 min estimate.

## Fast architecture

- **Ledger:**
  - **one** pass (was three);
  - a sequential reader cuts 8 MiB blocks of complete lines, and workers strict-parse
    and schema-check every row (`MEMBERSHIP_KEYS`, value types, plan file and
    allocation);
  - workers return compact numpy columns (file, row, decision, allocation, bytes) plus
    the family and duplicate-group ids of excluded rows only;
  - the parent integrates the blocks strictly in order;
  - the allocation key is cached per worker;
  - total rows must equal the plan's documents;
  - per-allocation decisions must equal the C05 completion when it is present (new).
- **Fact units:** read once, in plan order. Lineage keys are interned in the
  reference's exact order (plan file, then ledger order), so key indices, tie orders
  and every report list are identical. Blobs are copied once per unit, not sliced per
  key. A unit whose row count differs from the plan, or a corrupt unit header, refuses.
- **Protected index:** one parallel pass. Every record is validated (JSON object,
  `tokens` list, `provenance` list).
- **Corpus:**
  - reads only the target's majority allocation (every row, for the state breakdown)
    plus the files holding its other members;
  - each file is read sequentially, hashed, and must equal the C05 plan (size,
    SHA-256, rows). That integrity check is new;
  - workers parse the needed rows; the parent integrates in order.
  - Correction: the reference also scanned every row of every passenger allocation
    (in production, all UltraX files) and mixed those rows into the SYNTH state
    counts. The breakdown is now one allocation's, named in
    `corpus_attribution.allocation`. URL-key attribution of members is unchanged and
    verified equal.
- **Graph:**
  - integer node ids;
  - edges built as numpy arrays by class (duplicate star, parent edges, key postings
    with the reference's anchor rule);
  - connectivity via C05's own exact `grouping.components`;
  - stages replay incrementally from the previous partition;
  - removal probes share one base partition without all probed keys and add the
    others back: exact, with no rebuild per counterfactual;
  - the one-hop counterfactual is vectorized.
- **Workers:** one `OrderedPool` (spawned processes) for the whole run; `--workers 1`
  runs in-process. The count is operational only.
- **Passes:**
  - ledger 1 (was 3);
  - fact units 1;
  - index 1;
  - each needed corpus file 1 (and far fewer files than before).
- **Memory:**
  - parent ledger columns about 19 B/row (about 0.3 GB at 15.1M rows);
  - interned ids for excluded rows only;
  - the target graph as integer arrays;
  - bounded block queues (2 x workers blocks of 8 MiB);
  - measured peak tree RSS 1.1 GiB at 8 workers on the bench; projected 3-4 GiB in
    production.

## Performance (authored, bounded)

Fixture: `python -m scripts.c05_forensics_benchmark build --root C:/t/fb-1m --documents
700000 --synth-rows 300000`. It is a real authored C05 run (100 s). Contents:

* 895,136 ledger rows (504 MB, 563 B/row, close to the production row size);
* a 291,902-document SYNTH giant component formed by an additional-seed-URL chain
  over 10,000 seeds, with 1 direct hit;
* 2 cross-source duplicate passengers;
* direct hits on 70 % of Common Pile rows;
* 1.2 GB of fact units;
* 1.42 GB of corpus read.

Machine: 5700X3D; C: NVMe. One run each; variance not measured. Raw data:
[matrix-895k.jsonl](../evidence/C05-FORENSICS-FAST/matrix-895k.jsonl).

| run | wall s | CPU cores | peak tree RSS | ledger stage | corpus stage | report |
|---|---:|---:|---:|---:|---:|---|
| reference | 39.03 | 0.99 | 951 MiB | n/a | n/a | oracle |
| fast, 1 (in-process) | 19.78 | 0.99 | 530 MiB | 6.03 s | about 10 s | `2f278744…` |
| fast, 2 | 12.66 | 2.20 | 678 MiB | 3.94 s | about 5 s | `2f278744…` |
| fast, 4 | 10.96 | 2.69 | 806 MiB | 2.59 s | about 5 s | `2f278744…` |
| **fast, 8** | **10.85** | 3.31 | 1,105 MiB | **2.33 s (384k rows/s, 216 MiB/s)** | 4.9 s (292 MiB/s) | `2f278744…` |
| fast, 16 | 11.46 | 4.20 | 1,655 MiB | 2.72 s | about 5 s | `2f278744…` |

**Remaining limit.** The corpus stage is parent-bound at about 290 MiB/s with 2 or
more workers: one sequential reader, the hash and the block hand-off. That is close
to the G: SATA read rate, so a riskier redesign would save little. The protected
index kernel parses 142k lines/s per worker (27.5 MiB/s); the authored index is tiny,
so this was measured separately.

### Production projection (8 workers)

| stage | basis | projected |
|---|---|---|
| ledger | 15.1M rows at 384k rows/s (about 8.5 GB; read-bound below about 220 MB/s from X:) | about 40 s |
| fact units | 2,035 unit headers + 1.7M members (5.8x the bench members) | about 10 s |
| protected index | about 2.2M lines (runbook example: 615k patterns at 27.7 %) at about 1.1M lines/s | about 2-5 s |
| corpus | SYNTH 1.75M rows / 6.65 GB plus 1-2 UltraX passenger files (about 1.2 GB each, 2 rows parsed) at about 0.29 GB/s; SATA floor about 18 s | about 30-35 s |
| graph, unions, counterfactuals, focus | 1.1 s for 292k members, scaled | about 10 s |
| **total** | | **about 1.5-2.5 min** |

The reference projects to about 9-11 min:

* 45M ledger parses: about 6 min;
* single-threaded SYNTH + all UltraX rows: about 2.5 min;
* Python graph work: 1-2 min.

The live ETA lines show the real figures within the first 10 s of each long stage.

## Correctness

- **Oracle identity:**
  - `tests/test_c05_forensics_fast.py` compares 22 component fields and then the whole
    report with the reference, on a real authored C05 run;
  - the benchmark comparison did the same on the 895k-row fixture (8 and 16 workers).
  - Covered: staged union, each class alone, removal probes, counterfactuals, direct vs
    propagated hits, hit roots, dominant keys, edge classes and allocation pairs,
    posting histogram, non-majority members, focus allocations, decisions and the
    reconstruction flag.
- **Worker identity:** the report bytes are equal at 1/2/4/8/16 workers (tests and
  bench). Progress settings never change a report byte (tested).
- **Content-free:**
  - progress and refusals carry only fixed stage names, numbers and allocation names;
  - the report passes a self-check before printing (no `http`, no `://`, no 32+ hex run
    outside `plan_digest`/`plan_code_commit`), otherwise it refuses;
  - tests scan stderr for document ids, URLs, seed names, the planted benchmark prompt,
    the salt and paths.
- **Refusals** (exit 1, stdout empty, stderr one content-free JSON record with a fixed
  stage), each tested:
  - malformed or short ledger;
  - corrupt fact unit;
  - non-JSON or malformed protected index record;
  - a corpus file changed since C05;
  - a ledger that disagrees with the completion.
- **Interrupts:** Ctrl-C (exit 130) and a worker exception terminate and reap every
  child (tested: no child process remains). Nothing is written by the tool.
- **Operator note:** PowerShell creates the redirect target even for a failed run
  (empty or partial). Always check `$LASTEXITCODE` (0 = report), then
  `target_component.reconstruction_complete`.

## Progress

Stages:

* PLAN VERIFY, INPUT DISCOVERY;
* DECISION LEDGER, MAJOR COMPONENT, FACT UNITS, BENCHMARK INDEX, CORPUS SCAN;
* GRAPH BUILD, UNION / COMPONENTS, COUNTERFACTUALS, EDGE ATTRIBUTION;
* FOCUS ANALYSIS, REPORT VERIFY, COMPLETE.

Lines are the shared `RunProgress` format:

* rows/docs/files/bytes done and total, %;
* rolling and average rate, MiB/s;
* workers busy;
* process-tree RSS and peak against machine RAM, CPU;
* stage and run elapsed time.

The ETA is stage-local: a 30 s rolling rate, shown after 10 s of samples, `--:--:--`
before that. Graph stages show nodes, keys, incidences, edges, components and the
largest component, with unions done out of a known total. Example (authored, 8
workers; full transcript in
[progress-example-w8.txt](../evidence/C05-FORENSICS-FAST/progress-example-w8.txt)):

```text
[FORENSICS] DECISION LEDGER | done | 895,136/895,136 rows (100.00%) | 0.47/0.47 GiB input | 387,002 rows/s avg | 210.8 MiB/s | workers 8/8 busy | results 61 | RSS 0.9/71.9 GiB (peak 0.9) | CPU 42% | elapsed 00:00:02 (run 00:00:02) | ETA 00:00:00
[FORENSICS] CORPUS SCAN | done | 461,024/461,024 docs (100.00%) | files 3/3 | 1.42/1.42 GiB input | 92,501 docs/s avg | 291.7 MiB/s | workers 8/8 busy | RSS 1.0/71.9 GiB (peak 1.0) | CPU 19% | elapsed 00:00:04 (run 00:00:08) | ETA 00:00:00
[FORENSICS] UNION / COMPONENTS | done | 6/6 unions (100.00%) | 55 unions/s avg | components 250,202 | largest 25 | RSS 1.0/71.9 GiB (peak 1.0) | ...
```

## Tests

`tests/test_c05_forensics_fast.py` (35 tests):

* oracle field and whole-report identity;
* workers 1/2/8/16;
* text and JSONL stderr progress with stdout equal to the `--no-progress` report;
* `--no-progress`;
* content-free progress;
* ledger, fact-unit, index and corpus refusals;
* interrupt and worker-failure reaping.

Also run:

* the existing `tests/test_c05_component_forensics.py` (14), now on the fast path;
* `tests/test_mix01_deficit_summary.py` (2);
* `tests/test_c05_progress.py`.

Total: **65 passed** (`-n 0`, 47 s). ruff and `ruff format` are clean; `mypy --strict`
on `src/xlm/data/exclusion/forensics.py` reports no issues; `git diff --check` is
clean. Not run: the full acceptance suite and any real-data command.

## Production command (X: and G: attached; read-only)

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m scripts.c05_component_forensics --plan G:/XLM/c05-clean-v1/p0001.json --read-corpus --focus-allocation common_pile_prose/common_pile_prose/project_gutenberg --focus-allocation synth_en_explanations/default/- --benchmark-index X:/C05-Protected/prepared-clean-v1/index.jsonl --workers 8 --progress-interval 5 | Set-Content -Encoding utf8 C:/XLM-scratch/c05-component-forensics.json
$LASTEXITCODE   # must be 0; then check target_component.reconstruction_complete
```
