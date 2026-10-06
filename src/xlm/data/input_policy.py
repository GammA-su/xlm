"""Explicit signed engineering envelope for the reviewed Mix-01 C07-v2 corpus.

Legacy constants remain unchanged. Permission to use this envelope is carried in
the freeze, training-data and execution identity, never inferred from free RAM.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import ensure_plain_path
from xlm.data.evidence_v2.canonical import digest

POLICY_ID = "training-input-policy-v2"
V2_RECORD_BYTES = 2048
V2_DOCUMENT_TOKENS = 1_048_576
V2_READ_TOKENS = 1_048_576
PINNED_TABLE_SHA = "8527d59938ff3d32abffa682b3fb338c4aa99caebc220acc276f014b64b39342"


@dataclass(frozen=True)
class TrainingInputPolicy:
    id: str = POLICY_ID
    index_schema: str = "c07-offsets-v2"
    token_file_bytes: int = 3 * 1024**3
    index_file_bytes: int = 4 * 1024**3
    shard_bytes: int = 6 * 1024**3
    aggregate_bytes: int = 24 * 1024**3
    sidecar_bytes: int = 65536
    token_table_bytes: int = 65536
    index_record_bytes: int = V2_RECORD_BYTES
    document_tokens: int = V2_DOCUMENT_TOKENS
    read_window_tokens: int = V2_READ_TOKENS
    components: int = 11
    documents: int = 6_000_000
    verification_rss_bytes: int = 16 * 1024**3
    verification_seconds: int = 1800
    scratch_bytes: int = 0
    document_order: str = "shard-native-only"

    def binding(self) -> dict[str, Any]:
        body = asdict(self)
        return {**body, "digest": digest(body)}


def policy_from_binding(value: Any) -> TrainingInputPolicy | None:
    if value is None:
        return None
    policy = TrainingInputPolicy()
    if type(value) is not dict or value != policy.binding():
        raise ValueError("unknown or changed training input policy")
    return policy


def policy_fields(name: str | None) -> dict[str, Any]:
    if name is None:
        return {}
    if name != POLICY_ID:
        raise ValueError("unknown training input policy")
    return {"training_input_policy": TrainingInputPolicy().binding()}


def file_limits(policy: TrainingInputPolicy) -> dict[str, int]:
    return {
        "tokens.bin": policy.token_file_bytes,
        "offsets.jsonl": policy.index_file_bytes,
        "token_bytes.u16": policy.token_table_bytes,
        "shard_manifest.json": policy.sidecar_bytes,
        "shard_counters.json": policy.sidecar_bytes,
        "c05-attestation.json": policy.sidecar_bytes,
    }


def admit_sizes(sizes: Mapping[str, Mapping[str, int]], policy: TrainingInputPolicy) -> int:
    """Pure finite admission, also used by sparse-layout tests and diagnostics."""
    if not 1 <= len(sizes) <= policy.components:
        raise ValueError("training input component ceiling")
    limits = file_limits(policy)
    total = 0
    for files in sizes.values():
        if set(files) != set(limits):
            raise ValueError("training input file set differs from the v2 policy")
        if any(type(n) is not int or not 0 <= n <= limits[k] for k, n in files.items()):
            raise ValueError("training input per-file ceiling")
        subtotal = sum(files.values())
        if subtotal > policy.shard_bytes:
            raise ValueError("training input per-shard ceiling")
        total += subtotal
    if total > policy.aggregate_bytes:
        raise ValueError("training input aggregate ceiling")
    return total


def admit_sources(sources: Mapping[str, Path], policy: TrainingInputPolicy) -> int:
    """Stat ALL components before any full hash, mapping or metadata allocation."""
    if not 1 <= len(sources) <= policy.components:
        raise ValueError("training input component ceiling")
    sizes = {}
    for component, directory in sources.items():
        ensure_plain_path(directory)
        entries = list(directory.iterdir())
        if len(entries) != len(file_limits(policy)):
            raise ValueError("training input unexpected file set")
        files = {}
        for path in entries:
            ensure_plain_path(path)
            if not path.is_file():
                raise ValueError("training input must contain regular files")
            files[path.name] = path.stat().st_size
        sizes[component] = files
    return admit_sizes(sizes, policy)


def check_frozen_policy(body: Mapping[str, Any]) -> TrainingInputPolicy | None:
    policy = policy_from_binding(body.get("training_input_policy"))
    if policy is None:
        return None
    if body["mode"] == "protected":
        pins = {
            "plan_digest": "c15b54542238b0a4d6cce371c81ce0fe71caed5ed7c6bc23f937e4e85bbb8c59",
            "completion_digest": "df9834ab459bf00fac7ee4ffe69503432fc8e95b9af15009dcca7897a81c0a27",
            "selection_digest": "17b1cfce2ce643ecb72d53f367f95ade00f1a842d23a242c6f3a234c74c34662",
            "counts_digest": "e0348ee8c07ab6c71ed6f5c95e2317fdca48407d020ce4b51b9f3aea22fab8e2",
            "valid_targets": 6_000_000_000,
        }
        if any(body.get(k) != v for k, v in pins.items()) or body["tokenizer"]["fingerprint"] != (
            "50bf9d45f6f6fa88062483d0e83f3be419e610dee6b75d37809252504bac5b54"
        ):
            raise ValueError("training input policy is reviewed only for pinned Mix-01")
    elif body["mode"] != "authored":
        raise ValueError("training input policy requires a protected or authored freeze")
    return policy


def validate_headers(
    sources: Mapping[str, Path], policy: TrainingInputPolicy, expected_table_sha: str | None = None
) -> None:
    """Small metadata/table admission; no tokens/index payload is read."""
    from xlm.data.tokens import TokenShardReader

    documents = 0
    for path in sources.values():
        reader = TokenShardReader(path)
        if (
            reader.index_schema != policy.index_schema
            or reader.manifest.token_dtype != "uint16"
            or reader.manifest.endianness != "little"
        ):
            raise ValueError("training policy requires uint16 c07-offsets-v2 shards")
        documents += reader.manifest.num_documents
        if (
            not 0 < reader.manifest.num_documents <= policy.documents
            or documents > policy.documents
        ):
            raise ValueError("training input document ceiling")
        if (
            reader.manifest.num_tokens
            != reader.counters["valid_targets"] + reader.manifest.num_documents
        ):
            raise ValueError("training input token total differs from positive-document rule")
        reader.token_byte_table()
        if (
            expected_table_sha is not None
            and reader.counters["token_bytes_sha256"] != expected_table_sha
        ):
            raise ValueError("training v2 byte table differs from the exact frozen tokenizer")
