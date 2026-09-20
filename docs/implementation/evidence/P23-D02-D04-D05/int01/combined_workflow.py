"""Combined D02 -> D04/D05 authored loopback workflow (integration evidence).

Bounded acquisition of authored official-shape records over loopback HTTP,
through the real public CLI (plan / fetch / status / verify), followed by a
declared local evaluation-input manifest that references the real acquisition
receipt, verify-only, a tiny-CPU evaluation, and a deliberately incomplete
(--limit 1) run. All fixtures are authored synthetic content in the official
record shape; nothing is downloaded, official, or research evidence.

Usage (from the integration worktree with its venv active):
    python docs/implementation/evidence/P23-D02-D04-D05/int01/combined_workflow.py \
        --workdir data/audit/p23-remediation/stage05/int01/combined
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
ITEM_IDS = ["int_arc_0000", "int_arc_0001", "int_arc_0002"]


def authored_records() -> list[dict[str, object]]:
    questions = [
        ("What colour is the integration sky?", ["blue", "plaid"], "A"),
        ("How many moons orbit the integration planet?", ["two", "nine"], "A"),
        ("Which integration tool measures heat?", ["a ruler", "a thermometer"], "B"),
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
        self.send_header("ETag", '"integration-v1"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def cli(args: list[str], home: Path, timeout: int, success: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["XLM_HOME"] = str(home)
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env.pop("HF_TOKEN", None)
    env.pop("HUGGING_FACE_HUB_TOKEN", None)
    env["HF_HOME"] = str(home / "hf_home")
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
    print(f"$ xlm {' '.join(args)} -> exit {result.returncode}", flush=True)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    options = parser.parse_args()
    work = Path(options.workdir).resolve()
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)

    Handler.body = ("\n".join(json.dumps(r) for r in authored_records()) + "\n").encode()
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{service.server_port}"
    try:
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
                            "revision": "authored-loopback-v1",
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
        plan_id, plan_hash = plan["plan_id"], plan["plan_hash"]
        print(f"plan {plan_id} hash {plan_hash}", flush=True)
        cli(["data", "fetch", "--plan", str(plan_path)], home, 60, success=False)
        cli(["data", "fetch", "--plan", str(plan_path), "--pilot-approved"], home, 120)
        status = json.loads(cli(["data", "status", "--plan", str(plan_path), "--json"], home, 60).stdout)
        assert status["records_acquired"] == 3, status
        raw = home / "acquisition" / plan_id / "raw"
        first = json.loads(
            cli(["data", "verify", "--plan", str(plan_path), "--output-dir", str(raw), "--json"], home, 60).stdout
        )
        second = json.loads(
            cli(["data", "verify", "--plan", str(plan_path), "--output-dir", str(raw), "--json"], home, 60).stdout
        )
        assert first == second, "republication must be identical"
        assert first["files"][0]["record_count"] == 3, first
        assert first["eligibility"] == "pilot_only", first
        receipt_id = first["receipt_id"]
        print(f"receipt {receipt_id}", flush=True)

        selected = raw / "selected_records.jsonl"
        rows = [json.loads(line) for line in selected.read_bytes().splitlines()]
        assert [r["id"] for r in rows] == ITEM_IDS, rows
        assert [r["_xlm_acquisition"]["row_index"] for r in rows] == [0, 1, 2]
        assert {r["_xlm_acquisition"]["source_file"] for r in rows} == {"arc.jsonl"}
        assert {r["_xlm_acquisition"]["selection_hash"] for r in rows} == {plan_hash}
        digest = hashlib.sha256(selected.read_bytes()).hexdigest()

        from xlm.evaluation.harness import harness_version
        from xlm.evaluation.inputs import SuiteTier, build_evaluation_inputs

        manifest = build_evaluation_inputs(
            scope_label="integration loopback arc scope v1",
            scope_kind="authored_fixture",
            exposure_class="authored_fixture",
            tier=SuiteTier.SEARCH,
            harness_version=harness_version(),
            entries=[
                {
                    "task": "arc_easy",
                    "leaf_task": "arc_easy",
                    "source_repository": "loopback-authored-integration",
                    "source_revision": "authored-loopback-v1",
                    "source_split": "train",
                    "record_schema_version": "arc_easy.official_shape.v1",
                    "adapter_version": "xlm_eval_json.v1",
                    "item_id_field": "id",
                    "data_file": str(selected),
                    "source_config": "ARC-Easy",
                    "label_field": "answerKey",
                    "notes": ["SYNTHETIC loopback content acquired via D02; never official data"],
                }
            ],
            base_dir=work,
            acquisition_receipts=[receipt_id],
            notes=["D02+D04/D05 integration workflow; authored fixture, not research evidence"],
        )
        manifest_path = work / "manifest.yaml"
        import yaml

        manifest_path.write_text(
            yaml.safe_dump(manifest.to_dict(), sort_keys=False), encoding="utf-8"
        )
        print(f"manifest {manifest.manifest_id()} receipts {list(manifest.acquisition_receipts)}", flush=True)
        assert manifest.selections[0].content_sha256 == digest

        verify_only = cli(
            ["evaluate", "--suite", "search", "--inputs", str(manifest_path), "--verify-inputs-only"],
            home, 120,
        )
        assert "Evaluation inputs VERIFIED" in verify_only.stdout, verify_only.stdout
        assert "Nothing was evaluated" in verify_only.stdout

        from xlm.config.schemas import TransformerBaselineConfig
        from xlm.models.serialization import save_model_to_directory
        from xlm.models.transformer import TransformerBaseline

        ckpt = work / "ckpt"
        save_model_to_directory(
            TransformerBaseline(
                TransformerBaselineConfig(
                    architecture="transformer_baseline", vocab_size=260, num_layers=2,
                    hidden_size=64, num_attention_heads=4, intermediate_size=128,
                    context_length=128, attention_backend="eager",
                ),
                seed=42,
            ),
            ckpt,
        )
        full_out = work / "eval-full"
        full = cli(
            ["evaluate", str(ckpt), "--suite", "search", "--inputs", str(manifest_path),
             "--output-dir", str(full_out)],
            home, 600,
        )
        assert full.returncode == 0
        evidence_files = sorted(full_out.glob("evidence_*.json"))
        assert evidence_files, sorted(p.name for p in full_out.iterdir())
        evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
        assert evidence["coverage"]["complete"] is True, evidence["coverage"]
        assert evidence["tasks"]["arc_easy"]["scored_items"] == 3
        assert evidence["tasks"]["arc_easy"]["expected_items"] == 3
        assert evidence["coverage"]["research_eligible"] is False
        assert receipt_id in evidence_files[0].read_text(encoding="utf-8"), "receipt lineage"
        assert evidence["coverage"]["manifest_id"] == manifest.manifest_id()

        limited_out = work / "eval-limited"
        limited = cli(
            ["evaluate", str(ckpt), "--suite", "search", "--inputs", str(manifest_path),
             "--limit", "1", "--output-dir", str(limited_out)],
            home, 600,
        )
        assert limited.returncode == 0
        assert "Index WITHHELD" in limited.stdout, limited.stdout
        limited_evidence = json.loads(sorted(limited_out.glob("evidence_*.json"))[0].read_text())
        assert limited_evidence["coverage"]["complete"] is False

        published = home / "raw_dataset"
        assert published.is_dir(), sorted(home.iterdir())
        lineage = {
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "receipt_id": receipt_id,
            "selection_hash": plan_hash,
            "artifact_sha256": digest,
            "manifest_id": manifest.manifest_id(),
            "complete_scored": evidence["tasks"]["arc_easy"]["scored_items"],
            "complete_expected": evidence["tasks"]["arc_easy"]["expected_items"],
            "limited_complete": limited_evidence["coverage"]["complete"],
        }
        (work / "lineage.json").write_text(json.dumps(lineage, indent=2), encoding="utf-8")
        print("LINEAGE " + json.dumps(lineage, sort_keys=True), flush=True)
        print("COMBINED WORKFLOW OK", flush=True)
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
