# Science-v1 comparison: synthetic-mixture-50m-confirmation

**SYNTHETIC EVIDENCE: authored values, not results.**

- Title: SYNTHETIC M0 vs M1 mixture
- Manifest hash: `ee8ff4af11415c61e4a7efa90e131de2f9f36fd3f9eff8eaadf0aa2b7314138f`
- Record hash: `943c17d07898818dbde7e772827aaa3147d02ded5efc64802f26f7f7f2218cc1`
- State: **PROVISIONAL**
- Versions: curve\_area=xlm-p35-linear-target-area-v1, decision=xlm-p35-decision-v1, evidence=xlm-science-run-evidence-v1, manifest=xlm-science-comparison-v1, promotion=xlm-p35-promotion-v1, statistics=xlm-p35-paired-seed-t-v1, tracks=xlm-science-tracks-v1
- Seed uncertainty: paired two-sided Student-t over independent training-seed pairs (Bonferroni); item-level uncertainty is separate and never decides.

## Summary (§U)

| comparison\_id | parent\_reference | track | changed\_variable | model | parameter\_count | target\_tokens | paired\_n | primary\_metric | primary\_control | primary\_candidate | paired\_delta | seed\_sd | seed\_ci | multiplicity | curve\_area\_delta | secondary | throughput | vram | failures | completeness | eligibility | decision | promotion\_state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synthetic-mixture-50m-confirmation | xlm-50m-baseline-dev@synthetic-0 | data\_mixture\_v1 | mixture\_components, exposure\_plan\_identity, data\_trace\_digest, per\_source\_exposure = \{'mixture\_components': \[\{'source\_id': 'prose', 'weight': 0.4\}, \{'source\_id': 'science', 'weight': 0.4\}, \{'source\_id': 'web', 'weight': 0.2\}\]\} | reference\_decoder L10 d512 h8 | 49883648 | 1000000000 | 4/5 pairs | equal\_domain\_text\_ce\_nats\_per\_token | 3.0875 | 3.065 | PROVISIONAL 4/5: -0.0225 \(candidate-control; improvement 0.0225, lower\_is\_better\) | 0.00957427 | PROVISIONAL 4/5: \[-0.0377348, -0.0072652\] t\(df=3\)=3.18245 | Bonferroni m=1 family synthetic-mixture-family; per-comparison level 0.95 | n/a | domain\_web\_text\_ce\_nats\_per\_token: guardrail pass | control NOT RUN / candidate NOT RUN \(declared\) | control NOT RUN / candidate NOT RUN \(declared\) | 1: m1-C4-failed \(failed\) | INCOMPLETE 4/5 \(missing: C4\) | ELIGIBLE | INCOMPLETE | PROVISIONAL |

## Candidate m1

- Promotion: **PROVISIONAL**: 4 of 5 fixed confirmation pairs complete: provisional progress only, never early success \(§H/§V\)
- Decision: **INCOMPLETE**: 4 of 5 registered pairs complete; missing or incomplete pairs are not dropped

### Pairs (explicit replicate identity)

| tuple | state | control | candidate | raw delta | attempts |
|---|---|---|---|---|---|
| C0 | COMPLETE | 3 | 2.98 | -0.02 | m0-C0:complete; m1-C0:complete |
| C1 | COMPLETE | 3.1 | 3.07 | -0.03 | m0-C1:complete; m1-C1:complete |
| C2 | COMPLETE | 3.2 | 3.19 | -0.01 | m0-C2:complete; m1-C2:complete |
| C3 | COMPLETE | 3.05 | 3.02 | -0.03 | m0-C3:complete; m1-C3:complete |
| C4 | MISSING | NOT RUN | NOT RUN | n/a | m0-C4:complete |

## All attempts (failed and excluded attempts stay visible)

| label | arm | tuple | state | run | reasons |
|---|---|---|---|---|---|
| m0-C0 | m0 | C0 | complete | run-m0-C0 |  |
| m1-C0 | m1 | C0 | complete | run-m1-C0 |  |
| m0-C1 | m0 | C1 | complete | run-m0-C1 |  |
| m1-C1 | m1 | C1 | complete | run-m1-C1 |  |
| m0-C2 | m0 | C2 | complete | run-m0-C2 |  |
| m1-C2 | m1 | C2 | complete | run-m1-C2 |  |
| m0-C3 | m0 | C3 | complete | run-m0-C3 |  |
| m1-C3 | m1 | C3 | complete | run-m1-C3 |  |
| m0-C4 | m0 | C4 | complete | run-m0-C4 |  |
| m1-C4-failed | m1 | n/a | failed | n/a | attempt failed: SYNTHETIC OOM; kept visible, never counted |
