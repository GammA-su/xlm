# Science-v1 comparison: synthetic-microbatch-confirmation

**SYNTHETIC EVIDENCE: authored values, not results.**

- Title: SYNTHETIC B8 vs B16/B32 grouping
- Manifest hash: `3a6449f8be7c4a58a97dea0546baf10ee2f38baddf91bf2e306feec5caa92efb`
- Record hash: `ac3ca279a1107dc0cae5dfade65cc14868fa1c37b6bd24ec040bac5af7a0b22c`
- State: **CONFIRMED\_50M**
- Versions: curve\_area=xlm-p35-linear-target-area-v1, decision=xlm-p35-decision-v1, evidence=xlm-science-run-evidence-v1, manifest=xlm-science-comparison-v1, promotion=xlm-p35-promotion-v1, statistics=xlm-p35-paired-seed-t-v1, tracks=xlm-science-tracks-v1
- Seed uncertainty: paired two-sided Student-t over independent training-seed pairs (Bonferroni); item-level uncertainty is separate and never decides.

## Summary (§U)

| comparison\_id | parent\_reference | track | changed\_variable | model | parameter\_count | target\_tokens | paired\_n | primary\_metric | primary\_control | primary\_candidate | paired\_delta | seed\_sd | seed\_ci | multiplicity | curve\_area\_delta | secondary | throughput | vram | failures | completeness | eligibility | decision | promotion\_state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synthetic-microbatch-confirmation | xlm-50m-baseline-dev@synthetic-0 | microbatch\_grouping\_v1 | microbatch\_sequences = \{'microbatch\_sequences': 16\} | reference\_decoder L10 d512 h8 | 49883648 | 1000000000 | 5/5 pairs | equal\_domain\_text\_ce\_nats\_per\_token | 3.1 | 3.1018 | 0.0018 \(candidate-control; improvement -0.0018, lower\_is\_better\) | 0.00083666 | \[0.000761149, 0.00283885\] t\(df=4\)=2.77645 | Bonferroni m=1 family synthetic-batch-family; per-comparison level 0.95 | 0.0018 | n/a | control 45000 / candidate 52000 \(declared\) | control 11 / candidate 11 \(declared\) | 0 | COMPLETE | ELIGIBLE | NON\_INFERIOR | CONFIRMED\_50M |

## Candidate b16

- Promotion: **CONFIRMED\_50M**: 50m confirmation satisfied the frozen §P rule; conditional on the fixed within-source document order \(no robustness claim\)
- Decision: **NON\_INFERIOR**: lower improvement bound -0.00283885 lies above minus the NI margin -0.02

### Pairs (explicit replicate identity)

| tuple | state | control | candidate | raw delta | attempts |
|---|---|---|---|---|---|
| C0 | COMPLETE | 3 | 3.001 | 0.001 | b8-C0:complete; b16-C0:complete |
| C1 | COMPLETE | 3.1 | 3.102 | 0.002 | b8-C1:complete; b16-C1:complete |
| C2 | COMPLETE | 3.2 | 3.203 | 0.003 | b8-C2:complete; b16-C2:complete |
| C3 | COMPLETE | 3.05 | 3.051 | 0.001 | b8-C3:complete; b16-C3:complete |
| C4 | COMPLETE | 3.15 | 3.152 | 0.002 | b8-C4:complete; b16-C4:complete |

## All attempts (failed and excluded attempts stay visible)

| label | arm | tuple | state | run | reasons |
|---|---|---|---|---|---|
| b8-C0 | b8 | C0 | complete | run-b8-C0 |  |
| b16-C0 | b16 | C0 | complete | run-b16-C0 |  |
| b8-C1 | b8 | C1 | complete | run-b8-C1 |  |
| b16-C1 | b16 | C1 | complete | run-b16-C1 |  |
| b8-C2 | b8 | C2 | complete | run-b8-C2 |  |
| b16-C2 | b16 | C2 | complete | run-b16-C2 |  |
| b8-C3 | b8 | C3 | complete | run-b8-C3 |  |
| b16-C3 | b16 | C3 | complete | run-b16-C3 |  |
| b8-C4 | b8 | C4 | complete | run-b8-C4 |  |
| b16-C4 | b16 | C4 | complete | run-b16-C4 |  |
