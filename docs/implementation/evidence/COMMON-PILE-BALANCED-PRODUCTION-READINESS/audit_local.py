"""Read-only local evidence audit. Emits identities and counts, never corpus text."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.adapters import common_pile_adapters, mix01_adapters
from xlm.data.adapters.rejections import serialize_document
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import certified_evidence as ce

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"


def main() -> None:
    spec = importlib.util.spec_from_file_location("mix01_source", ROOT / "scripts/mix01_source.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cli
    spec.loader.exec_module(cli)
    paths = [
        Path("D:/Project/xlm-operator-pilot/adapter-cert-common-pile01/real-records.jsonl"),
        Path("G:/XLM/calib/common_pile_cert02/real-records.jsonl"),
    ]
    counts: dict[str, int] = {}
    inputs = []
    documents: dict[str, Any] = {}
    adapter = common_pile_adapters.CommonPileAdapter()
    old = mix01_adapters.CommonPileAdapter()
    for path in paths:
        if path.stat().st_size > 16 * 1024**2:
            raise ValueError("certification evidence exceeds the bounded audit input")
        data = path.read_bytes()
        before = hashlib.sha256(data).hexdigest()
        count = 0
        for line in data.splitlines():
            row = json.loads(line)
            name, index = row["_cert_source_file"], row["_cert_source_row"]
            component = name.split("/")[0]
            upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
            assert set(upstream) == {"text"} and isinstance(upstream["text"], str)
            kwargs = dict(source_file=name, source_row=index, source_revision=REVISION)
            doc = adapter.adapt(upstream, **kwargs)
            assert serialize_document(doc) == serialize_document(old.adapt(upstream, **kwargs))
            documents.setdefault(name, hashlib.sha256()).update(
                serialize_document(doc).encode("utf-8") + b"\n"
            )
            counts[component] = counts.get(component, 0) + 1
            count += 1
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        inputs.append({"path": str(path), "sha256": before, "rows": count})
    receipt = json.loads((OUT / "certification-cert02.receipt.json").read_bytes())
    body = dict(receipt)
    digest = body.pop("digest")
    assert digest == canonical.digest(body)
    for entry in receipt["files"]:
        assert documents[entry["file"]].hexdigest() == entry["adapter"]["documents_sha256"]
    target = ArtifactStore(ArtifactPaths(root=Path("G:/XLM/xlm-home")))
    historical = {}
    for name in (
        "ultrax",
        "finepdfs",
        "synth",
        "wiki_rewrite",
        "finewiki",
        "ifm_general",
        "ifm_planning",
        "simple_stories",
    ):
        try:
            result = cli.current_admission(cli.spec_of(name), target)
        except (ValueError, RuntimeError, OSError) as exc:
            historical[name] = {"status": "BLOCK", "reason": str(exc)}
        else:
            historical[name] = {"status": "PASS", "identity": result}
    metadata = ce.generic_probe_record(target, "common_pile", "common_pile_prose")
    value = {
        "network": False,
        "real_local_replay": True,
        "certification_inputs": inputs,
        "components": counts,
        "adapter_v1_v2_byte_identical": True,
        "cert02_documents_reproduced": True,
        "cert02_digest": digest,
        "adapter_code_identity": ce.adapter_code_identity("common_pile"),
        "historical_admission_gates": historical,
        "common_pile_metadata_probe_present": metadata is not None,
    }
    (OUT / "local-audit.json").write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
