# Dataset discovery catalog and initial views

The following twenty repositories carry forward the requested recent-data shortlist. This file deliberately omits unverified release dates, token counts, permanent license claims and invented revision hashes. A name in this table is not an admitted production source. The acquisition code must verify the real schema/subset and record the source’s current revision and review evidence before use.

The catalog in `manifests/datasets.catalog.yaml` is JSON-compatible YAML for unambiguous machine reading. Its `subset_hints` are discovery hints; they cannot be passed straight to a loader without verification. All production approvals initially remain false.

| # | XLM source ID | Candidate repository | Intended investigation |
|---|---|---|---|
| 1 | `txt360_v2` | `IFM/TxT360-v2` | General web challenger; inspect web/Q&A lineage separately. |
| 2 | `ifm_behaviors` | `IFM/Pretrain-Behaviors` | Initial mixture candidate; subset schemas vary and require separate probing. |
| 3 | `ifm_math` | `IFM/Math-Reasoning` | Exploratory mathematical explanation/dialogue views; exact subsets must be probed. |
| 4 | `ifm_code` | `IFM/Code-Reasoning` | Exploratory code-related transfer, not an anchor allocation. |
| 5 | `institutional_books` | `institutional/institutional-books-hl-enriched-text` | Optional historical English prose; current usage terms need explicit review. |
| 6 | `institutional_newspapers` | `institutional/institutional-newspapers-bpl` | Optional historical prose/OCR-quality experiment; usage terms need review. |
| 7 | `telco_common` | `GSMA/Telco-Common-Corpus` | Exploratory technical-domain source with provenance/licensing audit. |
| 8 | `nemotron_cc21` | `nvidia/Nemotron-CC-v2.1` | These are category hints, not guaranteed dataset config names. Initial mixture selects organic only. |
| 9 | `nemotron_cc_code` | `nvidia/Nemotron-CC-Code-v1` | Optional code-related explanatory web source. |
| 10 | `nemotron_specialized` | `nvidia/Nemotron-Pretraining-Specialized-v1` | Initial mixture uses Wiki-Rewrite only; inspect component-specific fields/terms. |
| 11 | `synth` | `PleIAs/SYNTH` | Initial mixture: English self-contained explanations with necessary context; probe actual structured fields. |
| 12 | `finepdfs_edu` | `HuggingFaceFW/finepdfs-edu` | Initial mixture: English publisher-extracted educational PDF text; no automatic OCR. |
| 13 | `dolma3_science` | `allenai/dolma3_pool` | Science-PDF component challenger only; never ingest the whole mixed pool by default. |
| 14 | `finewiki` | `HuggingFaceFW/finewiki` | Initial mixture: English article prose; inspect actual language/source schema. |
| 15 | `nemotron_cc_math` | `nvidia/Nemotron-CC-Math-v1` | Exploratory mathematics-explanation component; verify hinted view. |
| 16 | `hplt3` | `HPLT/HPLT3.0` | Broad-text challenger; English subset/schema/dedup properties require audit. |
| 17 | `essential_web` | `EssentialAI/essential-web-v1.0` | Initial mixture with actual taxonomy/quality-based science, practical and prose selections. |
| 18 | `common_pile` | `common-pile/comma_v0.1_training_dataset` | Initial mixture selected complementary prose only; inspect source metadata/terms. |
| 19 | `simple_stories` | `SimpleStories/SimpleStories` | Initial mixture small coherent-story component; inspect topic/style/source fields. |
| 20 | `climbmix` | `nvidia/Nemotron-ClimbMix` | Optional pretokenized challenger; review use terms and original tokenization before retokenizing. |

## Initial views

Source-family weights must be decomposed into explicit exclusive views. The initial recipe uses:

- `essential_science`, `essential_practical`, `essential_prose`: selectors mapped to verified Essential-Web taxonomy/quality values; unknown values do not pass silently.
- `nemotron_organic_high`, `nemotron_organic_medium_high`: the appropriate organic metadata categories, not synthetic/translated siblings.
- `finepdfs_en`: coherent English extracted educational prose, with broken/context-dependent records filtered.
- `synth_en_explanations`: exact verified structured rendering with necessary supplied context and final explanation; long-reasoning inclusion is a separate treatment.
- `nemotron_wiki_rewrite`: only the verified Wikipedia-rewrite component.
- `finewiki_en`: English canonical article text.
- `ifm_behaviors_general_planning`: explicitly probed general/planning view with its own internal weighting/renderer recorded; proposed 50/50 internal valid-token split, subject to audit and recorded as an experimental choice.
- `common_pile_prose`: selected instructional/narrative/transcript sources with exclusions/provenance intact.
- `simple_stories`: coherent short stories with explicit topic/style selection.
- `txt360_web`: verified web-only view used by the replacement experiment, not automatically added to the anchor.

These names belong to XLM, not necessarily to the publisher. Do not invent corresponding metadata columns upstream. The implementation must report how conceptual buckets map to actual records and how overlapping membership is resolved.

## No silent substitutions

An unavailable/empty/unapproved view blocks the requested mixture. The separately named `mix01_no_ifm` is supplied as an explicit alternative, moving the IFM 5% into Essential practical prose. It must be selected by the operator; it is never substituted automatically. All mixture decisions remain subject to source-rights, provenance, contamination and sufficiency review.
