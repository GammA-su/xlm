"""Compare explicitly named local three-update diagnostics under frozen tolerances."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import torch
from benchmark_p33 import EVIDENCE, LOCAL, write_json


def compare(reference_name: str, names: list[str]) -> dict[str, Any]:
    reference = torch.load(LOCAL / f"{reference_name}.pt", map_location="cpu", weights_only=True)
    reference_report = json.loads((EVIDENCE / f"{reference_name}.json").read_text())
    rows = []
    for name in names:
        candidate = torch.load(LOCAL / f"{name}.pt", map_location="cpu", weights_only=True)
        report = json.loads((EVIDENCE / f"{name}.json").read_text())
        assert report["initial_parameters_sha256"] == reference_report["initial_parameters_sha256"]
        assert report["fixture"] == reference_report["fixture"]
        for field in ("global_targets", "steps", "warmup", "model"):
            assert report["args"][field] == reference_report["args"][field]
        row: dict[str, Any] = {"candidate": name, "status": "VERIFIED"}
        max_parameter_error = 0.0
        try:
            for key, expected in reference["parameters"].items():
                actual = candidate["parameters"][key]
                max_parameter_error = max(
                    max_parameter_error, float((actual - expected).abs().max())
                )
                torch.testing.assert_close(actual, expected, atol=2e-5, rtol=2e-4)
            expected_logits, actual_logits = (
                reference["initial_logits"],
                candidate["initial_logits"],
            )
            common_rows = min(expected_logits.shape[0], actual_logits.shape[0])
            expected_logits, actual_logits = (
                expected_logits[:common_rows],
                actual_logits[:common_rows],
            )
            torch.testing.assert_close(actual_logits, expected_logits, atol=0.032, rtol=0.003)
            row["max_initial_logit_error"] = float((actual_logits - expected_logits).abs().max())
            loss_errors = []
            for actual, expected in zip(
                report["metrics"], reference_report["metrics"], strict=True
            ):
                for key in ("valid_targets", "committed_valid_targets", "learning_rate", "step"):
                    assert actual[key] == expected[key]
                error = abs(actual["loss"] - expected["loss"])
                assert error <= 0.032 + 0.003 * abs(expected["loss"])
                loss_errors.append(error)
            row["loss_absolute_errors"] = loss_errors
        except AssertionError as exc:
            row.update(status="FAILED", error=str(exc))
        row["max_parameter_error"] = max_parameter_error
        rows.append(row)
    return {
        "reference": reference_name,
        "criteria": "P33 predeclared short-trajectory bounds",
        "rows": rows,
        "status": "VERIFIED" if all(row["status"] == "VERIFIED" for row in rows) else "FAILED",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("candidates", nargs="+")
    args = parser.parse_args()
    if not all(name.replace("_", "").isalnum() for name in [args.reference, *args.candidates]):
        parser.error("Only alphanumeric and underscore diagnostic names are accepted")
    torch.set_num_threads(1)
    result = compare(args.reference, args.candidates)
    case_id = hashlib.sha256(" ".join(args.candidates).encode()).hexdigest()[:12]
    output = EVIDENCE / f"equivalence_{args.reference}_{case_id}.json"
    if output.exists():
        raise FileExistsError(output)
    write_json(output, result)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "VERIFIED" else 1)
