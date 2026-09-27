"""Offline compatibility for the UltraX operator probe path (no network).

Covers the datasets-5.0.1 repairs with stubbed ``datasets`` /
``huggingface_hub`` modules whose signatures match the installed 5.0.1 API
exactly (no ``trust_remote_code`` parameter anywhere): the probe must call
them without that argument and must forward the resolved exact revision SHA
to every downstream operation. Also covers the deterministic no-BOM
sample writer, the virtual cert locator and the surgical freeze script.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml


def _load_script(name: str) -> Any:
    """Load a scripts/ operator tool by file path (scripts/ is not a package)."""
    import importlib.util

    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ultrax_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_probe = _load_script("ultrax_schema_probe")
_freeze = _load_script("ultrax_freeze_revision")

_describe_features = _probe._describe_features
_sample_rows = _probe._sample_rows
_verify_config = _probe._verify_config
cert_source_file = _probe.cert_source_file
probe_main = _probe.main
freeze_main = _freeze.main

RESOLVED_SHA = "a88527587389fd4ab352e9ad1273f4c0a234d8df"
OTHER_SHA = "c" * 40
REPO = "openbmb/UltraX-Preview"
CONFIG = "UltraX-Ultra-FineWeb"

STUB_ROWS = [
    {
        "uid": f"stub-uid-{index:02d}",
        "raw_content": f"stub raw {index}",
        "cleaned_content": f"stub cleaned {index}",
        "processed_functions": "keep_all",
        "source": "Ultra-FineWeb",
    }
    for index in range(3)
]


class _StubInfo:
    def __init__(self, features: dict[str, str]) -> None:
        self.features = features


class _StubBuilder:
    def __init__(self, features: dict[str, str]) -> None:
        self.info = _StubInfo(features)


def _install_stubs(monkeypatch: pytest.MonkeyPatch, calls: dict[str, Any]) -> None:
    datasets_stub = types.ModuleType("datasets")

    def get_dataset_config_names(path: str, revision: str | None = None) -> list[str]:
        calls["config_names"] = {"path": path, "revision": revision}
        return [CONFIG, "other-config"]

    def load_dataset_builder(
        path: str, name: str | None = None, revision: str | None = None
    ) -> Any:
        calls["builder"] = {"path": path, "name": name, "revision": revision}
        return _StubBuilder({field: f"string:{field}" for field in STUB_ROWS[0]})

    def load_dataset(
        path: str,
        name: str | None = None,
        split: str | None = None,
        streaming: bool = False,
        revision: str | None = None,
    ) -> Any:
        calls["load"] = {
            "path": path,
            "name": name,
            "split": split,
            "streaming": streaming,
            "revision": revision,
        }
        return iter(list(STUB_ROWS))

    datasets_stub.get_dataset_config_names = get_dataset_config_names  # type: ignore[attr-defined]
    datasets_stub.load_dataset_builder = load_dataset_builder  # type: ignore[attr-defined]
    datasets_stub.load_dataset = load_dataset  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", datasets_stub)

    hub_stub = types.ModuleType("huggingface_hub")

    class HfApi:
        def dataset_info(self, repo: str, timeout: float = 15.0) -> Any:
            calls.setdefault("alias_checks", []).append(repo)
            if repo == REPO:
                return types.SimpleNamespace(
                    sha=RESOLVED_SHA, license=None, cardData={"license": "apache-2.0"}
                )
            raise RuntimeError("404 Repository Not Found")

    hub_stub.HfApi = HfApi  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub_stub)


def test_helpers_forward_exact_revision_without_remote_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every datasets call drops trust_remote_code and pins the resolved SHA."""
    calls: dict[str, Any] = {}
    _install_stubs(monkeypatch, calls)
    assert _verify_config(REPO, CONFIG, revision=RESOLVED_SHA) == [CONFIG, "other-config"]
    assert calls["config_names"] == {"path": REPO, "revision": RESOLVED_SHA}
    features = _describe_features(REPO, CONFIG, revision=RESOLVED_SHA)
    assert sorted(features) == sorted(STUB_ROWS[0])
    assert calls["builder"] == {"path": REPO, "name": CONFIG, "revision": RESOLVED_SHA}
    rows, _ = _sample_rows(REPO, CONFIG, "train", 3, 60.0, revision=RESOLVED_SHA)
    assert len(rows) == 3
    assert calls["load"] == {
        "path": REPO,
        "name": CONFIG,
        "split": "train",
        "streaming": True,
        "revision": RESOLVED_SHA,
    }


def test_probe_main_receipt_and_sample_are_revision_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end probe with stubs: receipt, locator and sample share one SHA."""
    calls: dict[str, Any] = {}
    _install_stubs(monkeypatch, calls)
    receipt_path = tmp_path / "receipt.json"
    sample_path = tmp_path / "sample.jsonl"
    assert (
        probe_main(
            [
                "--probe-aliases",
                "--config",
                CONFIG,
                "--split",
                "train",
                "--max-rows",
                "3",
                "--timeout-seconds",
                "60",
                "--output",
                str(receipt_path),
                "--save-sample",
                str(sample_path),
            ]
        )
        == 0
    )
    assert calls["alias_checks"] == ["openbmb/UltraX-Preview", "openbmb/UltraX"]
    for key in ("config_names", "builder", "load"):
        assert calls[key]["revision"] == RESOLVED_SHA, key
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["repository"] == REPO
    assert receipt["revision_sha"] == RESOLVED_SHA
    assert receipt["config_verified"] is True
    raw = sample_path.read_bytes()
    assert raw[:1] == b"{"
    assert b"\r" not in raw
    lines = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    assert len(lines) == 3
    expected_locator = f"hf-stream://{REPO}@{RESOLVED_SHA}/{CONFIG}/train"
    for index, row in enumerate(lines):
        assert row["_cert_source_file"] == expected_locator
        assert row["_cert_source_row"] == index
        assert row["_cert_revision"] == RESOLVED_SHA


def test_cert_source_file_design() -> None:
    locator = cert_source_file(REPO, RESOLVED_SHA, CONFIG, "train")
    assert locator == f"hf-stream://{REPO}@{RESOLVED_SHA}/{CONFIG}/train"
    with pytest.raises(ValueError, match="40-hex"):
        cert_source_file(REPO, "main", CONFIG, "train")
    with pytest.raises(ValueError, match="token"):
        cert_source_file("openbmb/Ultra X", RESOLVED_SHA, CONFIG, "train")


def _minimal_views(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "kind": "mix01_view_registry",
                "registry_id": "mix01_views_v2",
                "views": [
                    {
                        "component_id": "ultrax_ultrafineweb",
                        "source_id": "ultrax_ultrafineweb",
                        "repository": REPO,
                        "observed_revision": None,
                        "observed_license": None,
                        "observed_configs": [CONFIG],
                        "adapter_id": "ultrax_ultrafineweb",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _minimal_catalog(path: Path) -> None:
    # JSON-style YAML, matching the real manifests/datasets.catalog.yaml.
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "source_catalog_draft",
                "catalog_id": "test_catalog",
                "sources": [
                    {
                        "candidate_number": 1,
                        "source_id": "ultrax_ultrafineweb",
                        "provider": "huggingface",
                        "repository": REPO,
                        "revision": None,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _receipt(revision: str, verified: bool = True) -> dict[str, Any]:
    return {
        "repository": REPO,
        "revision_sha": revision,
        "config": CONFIG,
        "config_verified": verified,
        "declared_license": "apache-2.0",
    }


def test_freeze_pins_surgically_and_is_idempotent(tmp_path: Path) -> None:
    views, catalog, receipt_path = (
        tmp_path / "views.yaml",
        tmp_path / "catalog.yaml",
        tmp_path / "receipt.json",
    )
    _minimal_views(views)
    _minimal_catalog(catalog)
    receipt_path.write_text(json.dumps(_receipt(RESOLVED_SHA)), encoding="utf-8")
    before_views, before_catalog = views.read_bytes(), catalog.read_bytes()
    assert (
        freeze_main(
            ["--receipt", str(receipt_path), "--views", str(views), "--catalog", str(catalog)]
        )
        == 0
    )
    assert RESOLVED_SHA.encode() in views.read_bytes()
    assert b"apache-2.0" in views.read_bytes()
    assert f'"revision": "{RESOLVED_SHA}"'.encode() in catalog.read_bytes()
    # Only the pin lines changed: reload and compare everything else.
    assert yaml.safe_load(views.read_text(encoding="utf-8"))["views"][0]["adapter_id"] == (
        "ultrax_ultrafineweb"
    )
    pinned_views, pinned_catalog = views.read_bytes(), catalog.read_bytes()
    assert pinned_views != before_views and pinned_catalog != before_catalog
    assert (
        freeze_main(
            ["--receipt", str(receipt_path), "--views", str(views), "--catalog", str(catalog)]
        )
        == 0
    )
    assert views.read_bytes() == pinned_views and catalog.read_bytes() == pinned_catalog


def test_freeze_refuses_other_sha_and_placeholders(tmp_path: Path) -> None:
    views, catalog, receipt_path = (
        tmp_path / "views.yaml",
        tmp_path / "catalog.yaml",
        tmp_path / "receipt.json",
    )
    _minimal_views(views)
    _minimal_catalog(catalog)
    receipt_path.write_text(json.dumps(_receipt(RESOLVED_SHA)), encoding="utf-8")
    assert (
        freeze_main(
            ["--receipt", str(receipt_path), "--views", str(views), "--catalog", str(catalog)]
        )
        == 0
    )
    pinned = (views.read_bytes(), catalog.read_bytes())
    receipt_path.write_text(json.dumps(_receipt(OTHER_SHA)), encoding="utf-8")
    assert (
        freeze_main(
            ["--receipt", str(receipt_path), "--views", str(views), "--catalog", str(catalog)]
        )
        == 1
    )
    assert (views.read_bytes(), catalog.read_bytes()) == pinned
    receipt_path.write_text(json.dumps(_receipt("main")), encoding="utf-8")
    assert (
        freeze_main(
            ["--receipt", str(receipt_path), "--views", str(views), "--catalog", str(catalog)]
        )
        == 1
    )
    assert (views.read_bytes(), catalog.read_bytes()) == pinned
    receipt_path.write_text(json.dumps(_receipt(RESOLVED_SHA, verified=False)), encoding="utf-8")
    assert (
        freeze_main(
            ["--receipt", str(receipt_path), "--views", str(views), "--catalog", str(catalog)]
        )
        == 1
    )
