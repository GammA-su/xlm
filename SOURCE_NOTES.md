# Source notes and implementation verification

Official documentation was checked on 18 September 2026 to ground the design. Implementers must resolve exact supported versions and source revisions in their actual environment. The supplied manifest is a discovery catalog, not an assertion that every provider, schema, license or subset has been live-tested by this pack.

- **uv + PyTorch:** `https://docs.astral.sh/uv/guides/integration/pytorch/` — explicit accelerator indexes, platform/extras policies and compatibility-sensitive GPU extensions. Do not copy a wheel version without testing the target machine.
- **uv locking:** `https://docs.astral.sh/uv/concepts/projects/sync/` — locked dependency execution.
- **HF dataset streaming:** `https://huggingface.co/docs/datasets/stream` — iterable checkpointing and the documented loss/refill of shuffle-buffer examples on resume. This is why strict replay uses frozen local shards and explicit cursor state.
- **HF download revisions:** `https://huggingface.co/docs/huggingface_hub/guides/download` — revision-based downloads, selected files and caching. XLM adds its own transfer and local-disk guards.
- **PyTorch reproducibility:** `https://docs.pytorch.org/docs/2.14/notes/randomness.html` — environmental/backend limits on reproducibility. This is an inspected documentation version, not a mandate to install PyTorch 2.14.
- **PyTorch attention:** `https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html` — verify mask, causal and dropout semantics for the installed revision before using a fast path.
- **Harness custom model guide:** `https://raw.githubusercontent.com/EleutherAI/lm-evaluation-harness/main/docs/model_guide.md` — likelihood, rolling likelihood, generation and continuation-boundary interfaces. Pin a commit rather than `main` in executable experiments.
- **Harness tasks:** `https://github.com/EleutherAI/lm-evaluation-harness/tree/main/lm_eval/tasks` — inspect and pin the actual BLiMP, ARC, HellaSwag and PIQA task definitions and scoring implementation.
- **Essential-Web:** `https://huggingface.co/datasets/EssentialAI/essential-web-v1.0` — text plus nested metadata/quality/taxonomy annotations. Conceptual XLM buckets must be mapped to real values, not invented dataset columns.
- **Nemotron CC v2.1:** `https://huggingface.co/datasets/nvidia/Nemotron-CC-v2.1` — organic versus synthetic/translated categories. Removed from the active Mix-01 by the UltraX migration (kept only as historical adapter/provenance reference); broad repository sampling is a different treatment.
- **UltraX (Ultra-FineWeb):** `https://huggingface.co/datasets/openbmb/UltraX-Preview` (probe alias `openbmb/UltraX` must be resolved explicitly, never silently chosen), config `UltraX-Ultra-FineWeb` — fields `uid`, `raw_content`, `cleaned_content`, `processed_functions`, `source`. Training text is `cleaned_content` verbatim; `raw_content` is never input; empty `cleaned_content` (`remove_all`) is a counted drop. UltraX is derived from source corpora and users must check applicable source-dataset licenses.
- **SYNTH:** `https://huggingface.co/datasets/PleIAs/SYNTH` — structured generated material; inspect actual fields and necessary context before rendering.
- **IFM behaviors:** `https://huggingface.co/datasets/IFM/Pretrain-Behaviors` — the card explicitly warns that fields/nested schemas vary by subset. A generic assumed `text` field is not sufficient.

These references support software/data-interface choices, not a claim that the proposed data mixture or model shapes achieve state of the art. Recipe weights, stage budgets, filter thresholds and acceptance gates are XLM proposals to test.
