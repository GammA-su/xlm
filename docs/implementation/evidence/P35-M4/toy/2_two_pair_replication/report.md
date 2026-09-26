# Science-v1 comparison: synthetic-mixture-50m-replication

**SYNTHETIC EVIDENCE: authored values, not results.**

- Title: SYNTHETIC M0 vs M1 mixture
- Manifest hash: `f744f23f89b968e7523a9968f4fd980bd78eaf227ab573bc1fab17806a16f696`
- Record hash: `d1cad49130b3946b77fe7b7b3361460357ce4e98c2b1e2597869e00d5ca7212b`
- State: **SCREEN\_ONLY**
- Versions: curve\_area=xlm-p35-linear-target-area-v1, decision=xlm-p35-decision-v1, evidence=xlm-science-run-evidence-v1, manifest=xlm-science-comparison-v1, promotion=xlm-p35-promotion-v1, statistics=xlm-p35-paired-seed-t-v1, tracks=xlm-science-tracks-v1
- Seed uncertainty: paired two-sided Student-t over independent training-seed pairs (Bonferroni); item-level uncertainty is separate and never decides.

## Summary (§U)

| comparison\_id | parent\_reference | track | changed\_variable | model | parameter\_count | target\_tokens | paired\_n | primary\_metric | primary\_control | primary\_candidate | paired\_delta | seed\_sd | seed\_ci | multiplicity | curve\_area\_delta | secondary | throughput | vram | failures | completeness | eligibility | decision | promotion\_state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synthetic-mixture-50m-replication | xlm-50m-baseline-dev@synthetic-0 | data\_mixture\_v1 | mixture\_components, exposure\_plan\_identity, data\_trace\_digest, per\_source\_exposure = \{'mixture\_components': \[\{'source\_id': 'prose', 'weight': 0.4\}, \{'source\_id': 'science', 'weight': 0.4\}, \{'source\_id': 'web', 'weight': 0.2\}\]\} | reference\_decoder L10 d512 h8 | 49883648 | 128000000 | 2/2 pairs | equal\_domain\_text\_ce\_nats\_per\_token | 3.43 | 3.385 | -0.045 \(candidate-control; improvement 0.045, lower\_is\_better\) | 0.00707107 | \[-0.108531, 0.018531\] t\(df=1\)=12.7062 | Bonferroni m=1 family synthetic-mixture-family; per-comparison level 0.95 | n/a | domain\_web\_text\_ce\_nats\_per\_token: guardrail pass | control NOT RUN / candidate NOT RUN \(declared\) | control NOT RUN / candidate NOT RUN \(declared\) | 0 | COMPLETE | ELIGIBLE | AMBIGUOUS | SCREEN\_ONLY |

## Candidate m1

- Promotion: **SCREEN\_ONLY**: replication evidence ranks hypotheses; it supplies no confirmatory claim at any scale
- Decision: **AMBIGUOUS**: improvement interval \[-0.018531, 0.108531\] crosses a decision boundary \(margin ±0.01\)

### Pairs (explicit replicate identity)

| tuple | state | control | candidate | raw delta | attempts |
|---|---|---|---|---|---|
| E0 | COMPLETE | 3.4 | 3.35 | -0.05 | m0-E0:complete; m1-E0:complete |
| E1 | COMPLETE | 3.46 | 3.42 | -0.04 | m0-E1:complete; m1-E1:complete |

## All attempts (failed and excluded attempts stay visible)

| label | arm | tuple | state | run | reasons |
|---|---|---|---|---|---|
| m0-E0 | m0 | E0 | complete | run-m0-E0 |  |
| m1-E0 | m1 | E0 | complete | run-m1-E0 |  |
| m0-E1 | m0 | E1 | complete | run-m0-E1 |  |
| m1-E1 | m1 | E1 | complete | run-m1-E1 |  |
