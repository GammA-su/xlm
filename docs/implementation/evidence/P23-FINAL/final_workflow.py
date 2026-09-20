"""Final-integration authored chain (tiny CPU/local fixtures, offline only).

local preparation/acquisition (loopback, public CLI)
  -> verified tokenizer + token shards (public CLI)
  -> tiny training / checkpoint / resume (public CLI)
  -> declared fixture evaluation, complete then --limit 1 (public CLI)
  -> D08 multi-seed comparison over shuffled input orders (public CLI)
  -> D07 single-copy export + fresh-process reload (public CLI + probe)
  -> runs report + chain lineage

Nothing is downloaded; no official benchmark content; no research evidence.
Usage: python docs/implementation/evidence/P23-FINAL/final_workflow.py
    --workdir data/audit/p23-remediation/stage06/final01/chain
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ITEM_IDS = ["chain_arc_0000", "chain_arc_0001", "chain_arc_0002"]


def cli(
    args: list[str], home: Path, timeout: int, success: bool = True
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["XLM_HOME"] = str(home)
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env.pop("HF_TOKEN", None)
    env.pop("HUGGING_FACE_HUB_TOKEN", None)
    env["HF_HOME"] = str(home / "hf_home")
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    print(f"$ xlm {' '.join(args[:6])}... -> exit {result.returncode}", flush=True)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def authored_arc_records() -> list[dict[str, object]]:
    questions = [
        ("What colour is the chain sky?", ["blue", "plaid"], "A"),
        ("How many moons orbit the chain planet?", ["two", "nine"], "A"),
        ("Which chain tool measures heat?", ["a ruler", "a thermometer"], "B"),
    ]
    return [
        {
            "id": item_id,
            "question": question,
            "choices": {"text": choices, "label": ["A", "B"]},
            "answerKey": answer,
        }
        for item_id, (question, choices, answer) in zip(ITEM_IDS, questions, strict=True)
    ]


class Handler(http.server.BaseHTTPRequestHandler):
    body: bytes = b""

    def log_message(self, *_: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path != "/arc.jsonl":
            self.send_response(404)
            self.end_headers()
            return
        body = Handler.body
        range_header = self.headers.get("Range")
        if range_header:
            start, end = map(int, range_header.removeprefix("bytes=").split("-"))
            total = len(body)
            part = body[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            body = part
        else:
            self.send_response(200)
        self.send_header("ETag", '"chain-v1"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def step_acquisition(work: Path, home: Path) -> dict[str, str]:
    Handler.body = ("\n".join(json.dumps(r) for r in authored_arc_records()) + "\n").encode()
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{service.server_port}"
        (work / "catalog.json").write_text(
            json.dumps(
                {
                    "catalog_id": "authored",
                    "sources": [
                        {
                            "candidate_number": 1,
                            "source_id": "authored",
                            "provider": "https",
                            "repository": url,
                            "revision": "authored-chain-v1",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (work / "ranges.json").write_text('{"arc.jsonl":[0,3]}', encoding="utf-8")
        plan_path = work / "plan.json"
        cli(
            ["data", "plan", "--source", "authored", "--catalog", str(work / "catalog.json"),
             "--files", "arc.jsonl", "--mode", "selected_records", "--row-ranges",
             str(work / "ranges.json"), "--max-bytes", "65536", "--max-records", "10",
             "--output", str(plan_path)],
            home, 60,
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        cli(["data", "fetch", "--plan", str(plan_path)], home, 60, success=False)
        cli(["data", "fetch", "--plan", str(plan_path), "--pilot-approved"], home, 120)
        status = json.loads(cli(["data", "status", "--plan", str(plan_path), "--json"], home, 60).stdout)
        assert status["records_acquired"] == 3, status
        raw = home / "acquisition" / plan["plan_id"] / "raw"
        first = json.loads(
            cli(["data", "verify", "--plan", str(plan_path), "--output-dir", str(raw), "--json"], home, 60).stdout
        )
        second = json.loads(
            cli(["data", "verify", "--plan", str(plan_path), "--output-dir", str(raw), "--json"], home, 60).stdout
        )
        assert first == second and first["files"][0]["record_count"] == 3
        assert first["eligibility"] == "pilot_only"
        rows = [json.loads(line) for line in (raw / "selected_records.jsonl").read_bytes().splitlines()]
        assert [r["id"] for r in rows] == ITEM_IDS
        assert [r["_xlm_acquisition"]["row_index"] for r in rows] == [0, 1, 2]
        print(f"ACQUISITION receipt={first['receipt_id']} plan_hash={plan['plan_hash']}", flush=True)
        return {"receipt_id": first["receipt_id"], "plan_hash": plan["plan_hash"],
                "selected": str(raw / "selected_records.jsonl")}
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


def step_tokenizer(work: Path, home: Path) -> dict[str, str]:
    res = cli(
        ["data", "import-local", "--manifest",
         str(REPO / "fixtures/sources/sample_local/manifest.yaml"), "--publish"],
        home, 120,
    )
    assert "canonical_sample_local" in res.stdout, res.stdout + res.stderr
    tok_dir = work / "tok"
    res = cli(
        ["tokenizer", "train", "--data-path", "canonical_sample_local",
         "--vocab-size", "260", "--type", "bpe", "--output-dir", str(tok_dir)],
        home, 300,
    )
    assert tok_dir.is_dir(), res.stdout + res.stderr
    pool_docs = work / "pool_docs.jsonl"
    with pool_docs.open("w", encoding="utf-8", newline="\n") as stream:
        for position, text in enumerate(
            ["The chain sky is blue at noon.", "Chain plants need light to grow."]
        ):
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            stream.write(json.dumps({
                "doc_id": f"chain-doc-{position}", "source_id": "authored-chain",
                "source_revision": "authored-chain-v1", "source_file": "pool_docs.jsonl",
                "source_row": position, "raw_hash": digest, "clean_hash": digest,
                "text": text, "utf8_byte_count": len(text.encode("utf-8")),
                "language": "en", "language_confidence": 1.0, "document_kind": "prose",
                "split": "train", "source_metadata": {}, "parent_ids": [],
                "license_reference": "authored-fixture", "transform_log": [],
                "quality_reasons": [], "cluster_ids": {},
            }) + "\n")
    shards = work / "shards"
    cli(["data", "tokenize", "--input", str(pool_docs),
         "--tokenizer", str(tok_dir), "--output-dir", str(shards)], home, 300)
    assert any(shards.iterdir()), "no shards produced"
    print(f"TOKENIZER dir={tok_dir} shards={shards}", flush=True)
    return {"tokenizer": str(tok_dir), "shards": str(shards)}


def compare_plans(work: Path) -> tuple[Path, Path, Path]:
    plan_body = {
        "track": "baseline",
        "horizon_kind": "standalone",
        "resolved_config": {
            "model": {"architecture": "transformer_baseline", "vocab_size": 64},
            "training": {
                "data_seed": 1, "init_seed": 2, "context_length": 16,
                "precision": "fp32", "budget": {"max_valid_targets": 100},
                "schedule": {"type": "warmup_cosine", "horizon_valid_targets": 100,
                             "warmup_valid_targets": 10, "min_lr_ratio": 0.1},
            },
            "data": {"mixture_preset": "mix01",
                     "mixture_details": {"id": "mix01", "weights": {"a": 1.0}},
                     "packing_policy": "causal_stream"},
            "objective": {"type": "cross_entropy"},
            "optimizer": {"type": "adamw", "tuning_allowance": 1},
        },
    }
    plan_a, plan_b = work / "cplan_a.json", work / "cplan_b.json"
    plan_a.write_text(json.dumps({**plan_body, "plan_id": "plan_a"}), encoding="utf-8")
    plan_b.write_text(json.dumps({**plan_body, "plan_id": "plan_b"}), encoding="utf-8")
    facts = work / "cfacts.json"
    facts.write_text(json.dumps({"total_params": 1000, "canonical_bytes": 500,
                                 "matched_bytes": None, "matched_compute": None,
                                 "measured_compute_seconds": 5.0}), encoding="utf-8")
    return plan_a, plan_b, facts


def train_plan(path: Path, plan_id: str) -> None:
    path.write_text(json.dumps({
        "schema_version": 1, "kind": "experiment_plan", "id": plan_id,
        "status": "planned", "track": "baseline",
        "model": {"architecture": "transformer_baseline", "vocab_size": 260,
                  "num_layers": 1, "hidden_size": 16, "num_attention_heads": 2,
                  "intermediate_size": 32, "context_length": 8,
                  "attention_backend": "eager", "tie_embeddings": True},
        "data": {"synthetic_tokens": [i % 28 + 4 for i in range(1000)],
                 "tokenizer_artifact": "byte"},
        "objective": {"type": "cross_entropy", "version": "1"},
        "optimizer": {"type": "adamw", "version": "1", "lr": 0.01, "weight_decay": 0.0},
        "training": {"device": "cpu", "precision": "fp32", "context_length": 8,
                     "global_batch_valid_targets": 16, "gradient_clip_norm": 1.0,
                     "budget": {"max_valid_targets": 32},
                     "schedule": {"type": "warmup_cosine", "warmup_valid_targets": 8,
                                  "horizon_valid_targets": 32}},
        "evaluation": {"suite": "search"}, "resources": {},
        "authorization": {"state": "authorized"},
        "code_hash": "code_hash_12345678", "dependency_hash": "dep_hash_12345678",
    }), encoding="utf-8")


def step_training(work: Path, home: Path) -> dict[str, str]:
    plan = work / "train_plan.json"
    train_plan(plan, "chain_train_01")
    res = cli(["train", str(plan), "--device", "cpu"], home, 600)
    assert "Training Run Complete" in res.stdout, res.stdout + res.stderr
    assert "Committed Targets:  32" in res.stdout
    chk = home / "checkpoints" / "run_chain_train_01_final"
    assert chk.is_dir(), sorted((home / "checkpoints").iterdir())
    res = cli(["resume", str(chk), "--fork", "--budget", "48", "--device", "cpu"], home, 600)
    assert res.returncode == 0, res.stdout + res.stderr
    print(f"TRAINING checkpoint={chk}", flush=True)
    return {"checkpoint": str(chk)}


def step_evaluation(work: Path, home: Path, selected: str, receipt_id: str) -> dict[str, str]:
    import yaml

    from xlm.evaluation.harness import harness_version
    from xlm.evaluation.inputs import SuiteTier, build_evaluation_inputs

    manifest = build_evaluation_inputs(
        scope_label="chain loopback arc scope v1",
        scope_kind="authored_fixture",
        exposure_class="authored_fixture",
        tier=SuiteTier.SEARCH,
        harness_version=harness_version(),
        entries=[{
            "task": "arc_easy", "leaf_task": "arc_easy",
            "source_repository": "loopback-authored-chain",
            "source_revision": "authored-chain-v1",
            "source_split": "train",
            "record_schema_version": "arc_easy.official_shape.v1",
            "adapter_version": "xlm_eval_json.v1",
            "item_id_field": "id", "data_file": selected,
            "source_config": "ARC-Easy", "label_field": "answerKey",
            "notes": ["SYNTHETIC loopback content acquired via D02; never official data"],
        }],
        base_dir=work,
        acquisition_receipts=[receipt_id],
        notes=["final-integration chain; authored fixture, not research evidence"],
    )
    manifest_path = work / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest.to_dict(), sort_keys=False), encoding="utf-8")
    res = cli(["evaluate", "--suite", "search", "--inputs", str(manifest_path),
               "--verify-inputs-only"], home, 120)
    assert "Evaluation inputs VERIFIED" in res.stdout, res.stdout + res.stderr

    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.serialization import save_model_to_directory
    from xlm.models.transformer import TransformerBaseline

    ckpt = work / "eval_ckpt"
    save_model_to_directory(
        TransformerBaseline(TransformerBaselineConfig(
            architecture="transformer_baseline", vocab_size=260, num_layers=2,
            hidden_size=64, num_attention_heads=4, intermediate_size=128,
            context_length=128, attention_backend="eager"), seed=42), ckpt)
    full_out = work / "eval-full"
    res = cli(["evaluate", str(ckpt), "--suite", "search", "--inputs", str(manifest_path),
               "--output-dir", str(full_out)], home, 600)
    assert res.returncode == 0, res.stdout + res.stderr
    evidence_files = sorted(full_out.glob("evidence_*.json"))
    assert evidence_files, sorted(p.name for p in full_out.iterdir())
    evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    assert evidence["coverage"]["complete"] is True
    assert evidence["tasks"]["arc_easy"]["scored_items"] == 3
    assert evidence["tasks"]["arc_easy"]["expected_items"] == 3
    assert evidence["coverage"]["research_eligible"] is False
    assert receipt_id in evidence_files[0].read_text(encoding="utf-8")
    assert evidence["coverage"]["manifest_id"] == manifest.manifest_id()
    limited_out = work / "eval-limited"
    res = cli(["evaluate", str(ckpt), "--suite", "search", "--inputs", str(manifest_path),
               "--limit", "1", "--output-dir", str(limited_out)], home, 600)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Index WITHHELD" in res.stdout, res.stdout
    limited = json.loads(sorted(limited_out.glob("evidence_*.json"))[0].read_text())
    assert limited["coverage"]["complete"] is False
    print(f"EVALUATION manifest={manifest.manifest_id()} scored=3/3 limited-incomplete", flush=True)
    return {"manifest_id": manifest.manifest_id()}


def write_seed_evidence(path: Path, fingerprint: str, init: int, data: int,
                         correct: dict[str, list[bool]]) -> None:
    chances = {"blimp": 0.5, "arc_easy": 0.25, "hellaswag": 0.25, "piqa": 0.5}
    tasks: dict[str, object] = {}
    for task_name, flags in correct.items():
        metric = "acc_norm" if task_name in ("arc_easy", "hellaswag") else "acc"
        tasks[task_name] = {
            "metric_name": metric, "chance": chances[task_name],
            "items": [{"item_id": f"{task_name}_{i}", "is_correct": bool(f),
                       "is_correct_normalized": bool(f), "omitted_reason": None}
                      for i, f in enumerate(flags)],
        }
    path.write_text(json.dumps({
        "identity": {"fingerprint": fingerprint, "checkpoint_hash": fingerprint,
                     "tokenizer_hash": "tok_fp_1"},
        "training_seeds": {"init_seed": init, "data_seed": data}, "tasks": tasks,
    }), encoding="utf-8")


def step_compare(work: Path, home: Path, plan_a: Path, plan_b: Path, facts: Path) -> dict[str, str]:
    neutral = {t: [True] * 4 for t in ("blimp", "arc_easy", "hellaswag", "piqa")}
    files = {}
    for name, fp, init, data, correct in [
        ("m1b", "fp_m1b", 5, 50, {**neutral, "arc_easy": [False] * 4}),
        ("m2b", "fp_m2b", 7, 70, {**neutral, "arc_easy": [True] * 4}),
        ("m1c", "fp_m1c", 5, 50, {**neutral, "arc_easy": [True] * 4}),
        ("m2c", "fp_m2c", 7, 70, {**neutral, "arc_easy": [False] * 4}),
    ]:
        files[name] = work / f"{name}.json"
        write_seed_evidence(files[name], fp, init, data, correct)
    clusters = work / "mclusters.json"
    clusters.write_text(
        json.dumps({f"blimp:blimp_{i}": f"blimp_sub_{i % 2}" for i in range(4)}), encoding="utf-8")

    def run_compare(b_order: list[str], c_order: list[str], tag: str) -> dict[str, object]:
        out = work / f"mcomparison_{tag}.json"
        args: list[str] = ["compare"]
        for name in b_order:
            args += ["--baseline", str(files[name])]
        for name in c_order:
            args += ["--candidate", str(files[name])]
        args += ["--plan-baseline", str(plan_a), "--plan-candidate", str(plan_b),
                 "--track", "architecture", "--facts-baseline", str(facts),
                 "--facts-candidate", str(facts), "--clusters", str(clusters),
                 "--n-bootstrap", "100", "--output", str(out)]
        res = cli(args, home, 300)
        assert res.returncode == 0, res.stdout + res.stderr
        return json.loads(out.read_text(encoding="utf-8"))

    first = run_compare(["m1b", "m2b"], ["m1c", "m2c"], "ab")
    second = run_compare(["m2b", "m1b"], ["m2c", "m1c"], "ba")
    assert first == second, "shuffled multi-seed input must compare identically"
    assert first["primary_seed_pair"] == [5, 50]
    assert first["bootstrap_version"] == "2"
    assert first["primary_selection_policy"] == "numeric_lexicographic_min_v1"

    from xlm.comparison.bootstrap import AlignedItem, _percentile, suite_bootstrap

    hand = (0.0 + 0.0 + 1.0) / 3
    assert hand == 1 / 3, "draw [A,A,B] mean is 1/3, not 1/2"
    items = [
        AlignedItem(item_id="blimp_A", task="blimp", cluster_id="A", score_a=0.0, score_b=0.0),
        AlignedItem(item_id="blimp_B", task="blimp", cluster_id="B", score_a=0.0, score_b=0.0),
        AlignedItem(item_id="blimp_C", task="blimp", cluster_id="C", score_a=0.0, score_b=1.0),
        AlignedItem(item_id="arc_easy_0", task="arc_easy", cluster_id="arc_easy",
                    score_a=1.0, score_b=1.0),
        AlignedItem(item_id="hellaswag_0", task="hellaswag", cluster_id="hellaswag",
                    score_a=1.0, score_b=1.0),
        AlignedItem(item_id="piqa_0", task="piqa", cluster_id="piqa", score_a=1.0, score_b=1.0),
    ]
    chances = {"blimp": 0.5, "arc_easy": 0.25, "hellaswag": 0.25, "piqa": 0.5}
    result = suite_bootstrap(items, chances, n_bootstrap=1000, ci_level=0.8, analysis_seed=7)
    assert (result.task_intervals["blimp"].ci_lo, result.task_intervals["blimp"].ci_hi) == (0.0, 2 / 3)
    print("COMPARE shuffled-identical primary=[5,50] blimp-CI=(0.0, 0.667)", flush=True)
    return {"index_difference": first["index_difference"]}


def step_export(work: Path, home: Path, checkpoint: str) -> dict[str, str]:
    out = work / "bundle"
    res = cli(["export", checkpoint, "--output-dir", str(out)], home, 300)
    assert res.returncode == 0, res.stdout + res.stderr
    manifest = json.loads((out / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["storage_layout"] == "single_copy", manifest
    assert manifest["export_format_version"] == "2", manifest
    payload_files = [p for p in out.iterdir() if p.suffix == ".safetensors"]
    assert len(payload_files) == 1, sorted(p.name for p in out.iterdir())
    header_len = struct.unpack("<Q", payload_files[0].read_bytes()[:8])[0]
    header = json.loads(payload_files[0].read_bytes()[8 : 8 + header_len])
    ranges = sorted(
        (tuple(v["data_offsets"]) for k, v in header.items() if k != "__metadata__"),
        key=lambda r: (r[0], r[1]),
    )
    assert ranges, "no payload ranges parsed"
    assert all(end > start for start, end in ranges)
    print(f"EXPORT layout=single_copy ranges={len(ranges)}", flush=True)
    probe = work / "reload_probe.py"
    probe.write_text(
        "import json, torch\n"
        "from xlm.export.loader import load_exported_model\n"
        "m, tok, man = load_exported_model(r'" + str(out) + "', device='cpu')\n"
        "m.eval()\n"
        "torch.manual_seed(20260920)\n"
        "ids = torch.randint(4, 260, (1, 8))\n"
        "out = {'ids': ids.tolist()}\n"
        "with torch.no_grad():\n"
        "    out['logits'] = m(ids).logits.tolist()\n"
        "out['tied'] = m.lm_head.weight is m.embed_tokens.weight\n"
        "out['deployed'] = int(m.count_parameters().unique_deployed)\n"
        "out['storage_layout'] = man.storage_layout\n"
        "print('RESULT' + json.dumps(out))\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["XLM_HOME"] = str(home)
    env["HF_HUB_OFFLINE"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    completed = subprocess.run(
        [sys.executable, str(probe)],
        cwd=REPO, env=env, capture_output=True, encoding="utf-8", errors="replace",
        check=False, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    fresh = json.loads(completed.stdout.split("RESULT", 1)[1])
    assert fresh["storage_layout"] == "single_copy", fresh
    assert fresh["tied"] is True, fresh

    from xlm.export.loader import load_exported_model as load_local

    local_model, _, local_manifest = load_local(out, device="cpu")
    local_model.eval()
    import torch as torch_local

    torch_local.manual_seed(20260920)
    with torch_local.no_grad():
        local_logits = local_model(torch_local.randint(4, 260, (1, 8))).logits.tolist()
    assert local_logits == fresh["logits"], "fresh-process numerical parity"
    assert local_manifest.storage_layout == "single_copy"
    print(f"RELOAD tied-fresh-process parity logits-match", flush=True)
    return {"export_dir": str(out), "deployed": fresh["deployed"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    options = parser.parse_args()
    work = Path(options.workdir).resolve()
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    acquired = step_acquisition(work, home)
    step_tokenizer(work, home)
    trained = step_training(work, home)
    step_evaluation(work, home, acquired["selected"], acquired["receipt_id"])
    plan_a, plan_b, facts = compare_plans(work)
    step_compare(work, home, plan_a, plan_b, facts)
    step_export(work, home, trained["checkpoint"])
    res = cli(["runs", "list"], home, 120)
    assert res.returncode == 0, res.stdout + res.stderr
    res = cli(["artifact", "verify", "canonical_sample_local"], home, 120)
    assert res.returncode == 0, res.stdout + res.stderr
    lineage = {
        "receipt_id": acquired["receipt_id"],
        "plan_hash": acquired["plan_hash"],
        "checkpoint": trained["checkpoint"],
        "chain": ["acquire", "tokenize", "train", "resume", "evaluate",
                  "compare", "export", "reload", "report"],
    }
    (work / "lineage.json").write_text(json.dumps(lineage, indent=2), encoding="utf-8")
    print("LINEAGE " + json.dumps(lineage, sort_keys=True), flush=True)
    print("FINAL CHAIN OK", flush=True)


if __name__ == "__main__":
    main()