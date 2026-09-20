import json
from pathlib import Path
from lm_eval import simple_evaluate
from xlm.evaluation.harness import build_task_manager, create_harness_model, materialize_pinned_tasks
from xlm.evaluation.suites import SuiteTier, TaskVariant
from xlm.models.serialization import load_model_for_inference
from xlm.tokenizers.byte import ByteTokenizer

v = TaskVariant(variant_id="xlm_blimp_search", lm_eval_task="blimp", tier=SuiteTier.SEARCH,
                split="train", normalized_metric="acc", chance_reference="fixed", chance_value=0.5,
                blimp_subdatasets=("blimp_syn_alpha",))
out = Path(".d0405/work/probe_tasks")
names, _ = materialize_pinned_tasks([v], out, build_task_manager(Path(".d0405/work/repro/tasks")))
m = load_model_for_inference(Path(".d0405/work/repro/model_ckpt"), device="cpu")
adapter = create_harness_model(model=m, tokenizer=ByteTokenizer(), device="cpu")
mgr = build_task_manager(out, include_defaults=False)
r = simple_evaluate(model=adapter, tasks=names, limit=2, task_manager=mgr,
                    log_samples=True, use_cache=None, bootstrap_iters=0)
print("TASKS " + json.dumps(names))
print("METRIC_KEYS " + json.dumps(sorted(r["results"][names[0]].keys())))
s = r["samples"][names[0]][0]
print("SAMPLE_KEYS " + json.dumps(sorted(s.keys())))
print("SAMPLE_ID " + json.dumps({k: s.get(k) for k in ("id", "doc_id", "target")}, default=str))
print("DOC " + json.dumps(s.get("doc"), default=str))
