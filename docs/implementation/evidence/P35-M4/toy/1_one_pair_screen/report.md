# Science-v1 comparison: synthetic-mixture-50m-screen

**SYNTHETIC EVIDENCE: authored values, not results.**

- Title: SYNTHETIC M0 vs M1 mixture
- Manifest hash: `4c744316b107a643262d9383001ca061860790359b2a94b4be35ca68a0d00aa5`
- Record hash: `bca1b2bafdec3139284f3d5c7b4ce9ebe28df0d4d7c5f02e1e796e8559a5b861`
- State: **SCREEN\_ONLY**
- Versions: curve\_area=xlm-p35-linear-target-area-v1, decision=xlm-p35-decision-v1, evidence=xlm-science-run-evidence-v1, manifest=xlm-science-comparison-v1, promotion=xlm-p35-promotion-v1, statistics=xlm-p35-paired-seed-t-v1, tracks=xlm-science-tracks-v1
- Seed uncertainty: paired two-sided Student-t over independent training-seed pairs (Bonferroni); item-level uncertainty is separate and never decides.

## Summary (§U)

| comparison\_id | parent\_reference | track | changed\_variable | model | parameter\_count | target\_tokens | paired\_n | primary\_metric | primary\_control | primary\_candidate | paired\_delta | seed\_sd | seed\_ci | multiplicity | curve\_area\_delta | secondary | throughput | vram | failures | completeness | eligibility | decision | promotion\_state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synthetic-mixture-50m-screen | xlm-50m-baseline-dev@synthetic-0 | data\_mixture\_v1 | mixture\_components, exposure\_plan\_identity, data\_trace\_digest, per\_source\_exposure = \{'mixture\_components': \[\{'source\_id': 'prose', 'weight': 0.4\}, \{'source\_id': 'science', 'weight': 0.4\}, \{'source\_id': 'web', 'weight': 0.2\}\]\} | reference\_decoder L10 d512 h8 | 49883648 | 128000000 | 1/1 pairs | equal\_domain\_text\_ce\_nats\_per\_token | 3.4 | 3.35 | -0.05 \(candidate-control; improvement 0.05, lower\_is\_better\) | n/a \(n=1\) | none \(n&amp;lt;2: no seed CI\) | Bonferroni m=1 family synthetic-mixture-family; per-comparison level 0.95 | n/a | domain\_web\_text\_ce\_nats\_per\_token: guardrail not\_shown | control NOT RUN / candidate NOT RUN \(declared\) | control NOT RUN / candidate NOT RUN \(declared\) | 0 | COMPLETE | ELIGIBLE | NO\_SEED\_INTERVAL | SCREEN\_ONLY |

## Candidate m1

- Promotion: **SCREEN\_ONLY**: screen evidence ranks hypotheses; it supplies no confirmatory claim at any scale
- Decision: **NO\_SEED\_INTERVAL**: fewer than two complete pairs: no seed-level interval, no inferential decision

### Pairs (explicit replicate identity)

| tuple | state | control | candidate | raw delta | attempts |
|---|---|---|---|---|---|
| E0 | COMPLETE | 3.4 | 3.35 | -0.05 | m0-E0:complete; m1-E0:complete |

## All attempts (failed and excluded attempts stay visible)

| label | arm | tuple | state | run | reasons |
|---|---|---|---|---|---|
| m0-E0 | m0 | E0 | complete | run-m0-E0 |  |
| m1-E0 | m1 | E0 | complete | run-m1-E0 |  |
