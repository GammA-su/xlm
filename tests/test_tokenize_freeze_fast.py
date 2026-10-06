"""C07 fast tokenize-selection and streamed freeze over a generated corpus (authored only).

The module fixture runs the operator chain once (C05 -> count -> select -> fast
``tokenize-selection`` (v2) -> fast ``freeze``). The reference single-process tokenizer
and the SQLite freeze are the oracles: v1 output must equal the reference byte for
byte, v2 output must be scientifically identical (tokens byte-identical, records equal
minus spans, re-derived spans and training-stream behavior identical).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV, execute

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import tokenfast as tf
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import C05Error
from xlm.data.tokens import (
    INDEX_SCHEMA_V1,
    INDEX_SCHEMA_V2,
    TOKEN_BYTES_FILE,
    TokenShardReader,
    derived_byte_spans,
)

FILES_V1 = ("tokens.bin", "offsets.jsonl", "shard_counters.json", "shard_manifest.json")
ATTESTATION = "c05-attestation.json"
load = canonical.loads_bytes_strict


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    monkey = pytest.MonkeyPatch()
    monkey.setenv(KEY_ENV, KEY)
    root = tmp_path_factory.mktemp("tokfast") / "root"
    summary = execute(root)
    reference = root / "reference-shards"
    common = ["--c05-proof", str(root / "proof.json"), "--tokenizer", str(root / "tokenizer")]
    assert (
        operator(
            [
                "tokenize-selection-reference",
                *common,
                "--selection",
                str(root / "selection"),
                "--output-root",
                str(reference),
            ]
        )
        == 0
    )
    yield {"root": root, "summary": summary, "reference": reference, "common": common}
    monkey.undo()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)


def components(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if not p.name.startswith("."))


def run_fast(flow: dict[str, Any], output: Path, **options: Any) -> dict[str, Any]:
    root = flow["root"]
    options.setdefault("workers", 1)
    options.setdefault("inline", options["workers"] == 1)
    return tf.tokenize_selection_fast(
        root / "proof.json",
        root / "selection",
        root / "tokenizer",
        output,
        scratch=options.pop("scratch", output.parent / (output.name + "-scratch")),
        output_reserve=0,
        scratch_reserve=0,
        **options,
    )


def assert_only_complete(root: Path, schema: str = INDEX_SCHEMA_V2) -> list[str]:
    """No stage directory or partial component may exist; returns the published names."""
    names = (*FILES_V1, ATTESTATION) + ((TOKEN_BYTES_FILE,) if schema == INDEX_SCHEMA_V2 else ())
    published = []
    for entry in root.iterdir() if root.exists() else []:
        assert tf.STAGE_MARK not in entry.name, "a stage directory survived"
        if entry.name.startswith("."):
            assert entry.name.endswith(".lock")
            continue
        assert sorted(p.name for p in entry.iterdir()) == sorted(names)
        TokenShardReader(entry).verify_integrity()
        published.append(entry.name)
    return sorted(published)


def same_tree(first: Path, second: Path) -> None:
    assert components(first) == components(second)
    for component in components(first):
        names = sorted(p.name for p in (first / component).iterdir())
        assert names == sorted(p.name for p in (second / component).iterdir())
        for name in names:
            assert (first / component / name).read_bytes() == (
                second / component / name
            ).read_bytes(), (component, name)


# -- exactness ----------------------------------------------------------------------------


@pytest.mark.parametrize(("workers", "inline"), [(1, True), (2, False), (4, False)])
def test_v1_schema_is_byte_identical_to_reference(
    flow: dict[str, Any], tmp_path: Path, workers: int, inline: bool
) -> None:
    out = tmp_path / "v1"
    result = run_fast(flow, out, workers=workers, inline=inline, index_schema=INDEX_SCHEMA_V1)
    same_tree(flow["reference"], out)  # tokens, index, counters, manifest, attestation
    assert sorted(result["shards"]) == components(flow["reference"])


def test_v2_schema_is_scientifically_identical_to_reference(flow: dict[str, Any]) -> None:
    reference, fast = flow["reference"], flow["root"] / "shards"
    assert components(reference) == components(fast)
    for component in components(reference):
        ref, v2 = TokenShardReader(reference / component), TokenShardReader(fast / component)
        v2.verify_integrity()
        assert (ref.index_schema, v2.index_schema) == (INDEX_SCHEMA_V1, INDEX_SCHEMA_V2)
        assert (reference / component / "tokens.bin").read_bytes() == (
            fast / component / "tokens.bin"
        ).read_bytes()
        ref_lines = (reference / component / "offsets.jsonl").read_bytes().splitlines()
        v2_lines = (fast / component / "offsets.jsonl").read_bytes().splitlines()
        assert len(ref_lines) == len(v2_lines)
        for old, new in zip(ref_lines, v2_lines, strict=True):
            record = json.loads(old)
            spans = record.pop("token_byte_spans")
            # The v2 line is exactly the reference line without the span list.
            assert json.dumps(record, ensure_ascii=False).encode() == new
            assert v2.with_byte_spans(json.loads(new))["token_byte_spans"] == spans
        counters = v2.counters
        assert counters.pop("index_schema") == INDEX_SCHEMA_V2
        assert len(counters.pop("token_bytes_sha256")) == 64
        assert counters == ref.counters
        manifest, expected = v2.manifest.to_dict(), ref.manifest.to_dict()
        assert manifest.pop("offsets_checksum_sha256") != expected.pop("offsets_checksum_sha256")
        assert manifest == expected


def test_every_document_is_recounted_and_crossings_are_exact_prefixes(
    flow: dict[str, Any],
) -> None:
    from xlm.data.exclusion.selection import iter_plan_documents, load_tokenizer
    from xlm.data.exclusion.transport import open_gate

    root, fast = flow["root"], flow["root"] / "shards"
    tokenizer = load_tokenizer(root / "tokenizer")
    with open_gate(root / "proof.json", allow_authored=True) as gate:
        assert gate is not None
        texts = {d.doc_id: d.text for _, d in iter_plan_documents(gate)}
    truncated = 0
    for component in components(fast):
        reader = TokenShardReader(fast / component)
        tokens = reader.read_tokens()
        for record in reader.iter_document_offsets():
            full, spans = tokenizer.encode_with_offsets(texts[record["doc_id"]], True)
            assert len(full) - 1 == record["c05_counted_valid_targets"]
            chosen = record["c05_selected_valid_targets"]
            start, count = record["token_start"], record["token_count"]
            assert count == chosen + 1 and record["valid_targets"] == chosen
            assert tokens[start : start + count] == full[: chosen + 1]
            lengths = reader.token_byte_table()
            assert (
                derived_byte_spans(full[: chosen + 1], lengths)
                == [list(s) for s in spans][: chosen + 1]
            )
            if chosen < record["c05_counted_valid_targets"]:
                truncated += 1
                assert record["eos_positions"] == []
    assert truncated == flow["summary"]["truncated_documents"] == 17


def test_v2_training_stream_equals_v1_stream(flow: dict[str, Any]) -> None:
    from xlm.data.sampling import MixtureRecipe
    from xlm.data.sampling.stream import MixtureBatcher
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    root = flow["root"]
    tokenizer = ByteLevelBPETokenizer.load(root / "tokenizer")
    recipe = MixtureRecipe.model_validate(
        load((root / "freeze/training-data.json").read_bytes())["mixture"]
    )
    runs = []
    for shards in (flow["reference"], root / "shards"):
        batcher = MixtureBatcher(
            recipe,
            {c: TokenShardReader(shards / c) for c in components(shards)},
            context_length=32,
            global_batch_valid_targets=512,
            microbatch_sequences=4,
            pad_token_id=tokenizer.pad_token_id,
            bos_token_id=tokenizer.bos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        steps = []
        for _ in range(12):
            batches = batcher.next_step_microbatches()
            batcher.commit()
            steps.append([json.dumps(vars(b), sort_keys=True, default=repr) for b in batches])
        state = json.dumps(batcher.get_state(), sort_keys=True, default=repr)
        runs.append((steps, state, batcher.share_report()))
    assert runs[0][0] == runs[1][0]  # identical windows, labels, masks and byte spans
    assert runs[0][1] == runs[1][1]  # identical trace digest and exposure counters
    assert runs[0][2] == runs[1][2] and runs[1][2]["byte_coverage_complete"] is True


def test_index_line_parts_equal_json_dumps() -> None:
    from xlm.core.contracts import CanonicalDocument

    doc = CanonicalDocument(
        doc_id='d"\\é\u2028\x00',
        source_id="src\n",
        source_revision="r",
        source_file="f",
        source_row=1,
        raw_hash="0" * 64,
        clean_hash="0" * 64,
        text="x",
        utf8_byte_count=1,
        language="en",
        language_confidence=0.5,
        document_kind="web",
        source_metadata={},
        parent_ids=[],
        license_reference="l",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={"duplicate_cluster": "ü\t", "split_group": 7},  # type: ignore[dict-item]
        split="train",
    )
    for spans in (None, [[0, 0], [0, 1], [1, 1]]):
        head, mid, tail = tf.index_line_parts(
            doc, "comp", 3, 1, spans, [0], [2], "r" * 64, "s" * 64, "c" * 64, 2, 2
        )
        record: dict[str, Any] = {
            "doc_id": doc.doc_id,
            "source_id": "comp",
            "token_start": 11,
            "token_count": 3,
            "byte_count": 1,
            "covered_bytes": 1,
            **({} if spans is None else {"token_byte_spans": spans}),
            "lineage_id": "ü\t",
            "split_group": 7,
            "byte_start": 5,
            "byte_end": 6,
            "valid_targets": 2,
            "bos_positions": [0],
            "eos_positions": [2],
            "split": "train",
            "c05_content": "c" * 64,
            "c05_receipt": "r" * 64,
            "c05_canonical_source_id": "src\n",
            "c05_selection": "s" * 64,
            "c05_counted_valid_targets": 2,
            "c05_selected_valid_targets": 2,
        }
        expected = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        assert tf.assemble_line(head, mid, tail, 11, 5, 6) == expected


def test_v2_token_byte_table_is_bound(flow: dict[str, Any], tmp_path: Path) -> None:
    shards = tmp_path / "shards"
    shutil.copytree(flow["root"] / "shards", shards)
    component = components(shards)[0]
    table = shards / component / TOKEN_BYTES_FILE
    raw = bytearray(table.read_bytes())
    raw[8] ^= 1
    table.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="Token byte table mismatch"):
        TokenShardReader(shards / component).verify_integrity()


# -- refusals, preflight, plan ----------------------------------------------------------------


def test_count_drift_refuses_and_publishes_nothing(flow: dict[str, Any], tmp_path: Path) -> None:
    from scripts.c05_authored_pilot import KEY as SIGNING

    from xlm.data.exclusion.artifacts import signed
    from xlm.data.exclusion.runner import file_sha

    root = flow["root"]
    drifted = tmp_path / "selection"
    shutil.copytree(root / "selection", drifted)
    rows = [load(r) for r in (drifted / "selected.jsonl").read_bytes().splitlines()]
    # A trusted-signer selection whose crossing record claims one more counted target:
    # every signed total still holds; only re-tokenizing the document can catch it.
    victim = next(r for r in rows if r["selected_valid_targets"] < r["counted_valid_targets"])
    victim["counted_valid_targets"] += 1
    raw = b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows)
    (drifted / "selected.jsonl").write_bytes(raw)
    body = load((drifted / "selection.json").read_bytes())["payload"]
    body.update(selected_membership_sha256=file_sha(drifted / "selected.jsonl"))
    body["selected_membership_bytes"] = len(raw)
    body.pop("issuer")
    (drifted / "selection.json").unlink()
    canonical.write_canonical_json(
        drifted / "selection.json", signed(body, ISSUER, SIGNING.encode())
    )
    out = tmp_path / "out"
    with pytest.raises(C05Error, match="exact token count drifted"):
        tf.tokenize_selection_fast(
            root / "proof.json",
            drifted,
            root / "tokenizer",
            out,
            scratch=tmp_path / "scratch",
            workers=1,
            inline=True,
            output_reserve=0,
            scratch_reserve=0,
        )
    assert victim["allocation"][0] not in assert_only_complete(out)


def test_preflight_refuses_before_any_source_read(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_source(*_: Any, **__: Any) -> Any:
        raise AssertionError("source read before preflight")

    real = shutil.disk_usage

    def tiny(path: Any) -> Any:
        usage = real(path)
        return type(usage)(usage.total, usage.total - 1, 1)

    monkeypatch.setattr(tf, "walk_rows", no_source)
    monkeypatch.setattr(tf.shutil, "disk_usage", tiny)
    with pytest.raises(C05Error, match="insufficient free space"):
        run_fast(flow, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_plan_only_is_exact_and_reads_no_source(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tf, "walk_rows", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    result = run_fast(flow, tmp_path / "out", plan_only=True)
    plan = result["plan"]
    selection = load((flow["root"] / "selection/selection.json").read_bytes())["payload"]
    assert (
        plan["token_ids"] == selection["selected_valid_targets"] + selection["selected_documents"]
    )
    assert plan["tokens_bin_bytes"] == 2 * plan["token_ids"]
    assert plan["valid_targets"] == 10_000 and not (tmp_path / "out").exists()
    actual = sum(
        (flow["root"] / "shards" / c / "offsets.jsonl").stat().st_size
        for c in components(flow["root"] / "shards")
    )
    assert sum(c["index_bytes_upper_bound"] for c in plan["components"].values()) >= actual


def test_output_root_must_be_fresh_and_is_locked(flow: dict[str, Any], tmp_path: Path) -> None:
    from filelock import FileLock

    out = tmp_path / "out"
    out.mkdir()
    (out / "stray").write_text("x")
    with pytest.raises(C05Error, match="not fresh"):
        run_fast(flow, out)
    (out / "stray").unlink()
    with FileLock(str(out / ".tokenize-selection.tokenize.lock")):
        with pytest.raises(C05Error, match="another tokenize-selection"):
            run_fast(flow, out)


# -- failures and resume ----------------------------------------------------------------


def _fail_on(n: int, error: BaseException) -> Any:
    calls = {"n": 0}

    def wrap(original: Any) -> Any:
        def failing(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == n:
                raise error
            return original(*args, **kwargs)

        return failing

    return wrap


def _tokenize_failure(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    original = tf.init_tokenize_worker
    calls = {"n": 0}

    def init(tables: Any) -> None:
        original(tables)
        backend = tf._STATE["tokenizer"]._tok

        class Failing:
            def __getattr__(self, name: str) -> Any:
                return getattr(backend, name)

            def encode_batch_fast(self, *args: Any, **kwargs: Any) -> Any:
                calls["n"] += 1
                if calls["n"] == n:
                    raise RuntimeError("injected tokenizer failure")
                return backend.encode_batch_fast(*args, **kwargs)

        tf._STATE["tokenizer"]._tok = Failing()

    monkeypatch.setattr(tf, "init_tokenize_worker", init)


FAILURES = {
    "read": lambda mp: mp.setattr(
        tf, "walk_rows", _fail_on(9, OSError("injected read"))(tf.walk_rows)
    ),
    "tokenization": lambda mp: _tokenize_failure(mp, 7),
    "packing": lambda mp: mp.setattr(
        tf.ShardStage, "write", _fail_on(6, OSError("injected write"))(tf.ShardStage.write)
    ),
    "index": lambda mp: mp.setattr(
        tf, "assemble_line", _fail_on(20, ValueError("injected index"))(tf.assemble_line)
    ),
    "final-write": lambda mp: mp.setattr(
        tf, "_write_synced", _fail_on(10, OSError("injected final write"))(tf._write_synced)
    ),
    "manifest": lambda mp: mp.setattr(
        tf, "_write_synced", _fail_on(12, OSError("injected manifest"))(tf._write_synced)
    ),
    "attestation": lambda mp: mp.setattr(
        tf, "signed", _fail_on(4, C05Error("injected attestation"))(tf.signed)
    ),
    "interrupt": lambda mp: mp.setattr(
        tf.ShardStage, "write", _fail_on(5, KeyboardInterrupt())(tf.ShardStage.write)
    ),
}


@pytest.mark.parametrize("point", sorted(FAILURES))
def test_failure_leaves_only_complete_shards_then_resume_is_exact(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    out, scratch = tmp_path / "out", tmp_path / "scratch"
    with monkeypatch.context() as patch:
        FAILURES[point](patch)
        with pytest.raises((C05Error, OSError, ValueError, RuntimeError, KeyboardInterrupt)):
            run_fast(flow, out, scratch=scratch)
    published = assert_only_complete(out)
    assert published != components(flow["root"] / "shards")
    assert not scratch.exists() or not any(scratch.iterdir())  # tokenizer copy removed
    if not out.exists():
        out.mkdir()
    result = run_fast(flow, out, scratch=scratch, resume=True)
    assert result["skipped"] == published
    same_tree(flow["root"] / "shards", out)


def test_resume_verifies_published_shards_and_refuses_tampering(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    shutil.copytree(flow["root"] / "shards", out)
    result = run_fast(flow, out, resume=True)
    assert result["skipped"] == components(out) and result["measured"]["seconds"] >= 0
    target = out / components(out)[0] / "tokens.bin"
    raw = bytearray(target.read_bytes())
    raw[0] ^= 1
    target.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="checksum mismatch"):
        run_fast(flow, out, resume=True)


def test_resume_removes_own_stale_stage_and_refuses_foreign_entries(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    shutil.copytree(flow["root"] / "shards", out)
    component = components(out)[-1]
    shutil.rmtree(out / component)
    stale = out / f".{component}{tf.STAGE_MARK}deadbeef"
    stale.mkdir()
    (stale / "tokens.bin").write_bytes(b"\x00\x01")
    result = run_fast(flow, out, resume=True)
    assert result["skipped"] == [c for c in components(out) if c != component]
    assert not stale.exists()
    same_tree(flow["root"] / "shards", out)
    (out / "foreign.txt").write_text("x")
    with pytest.raises(C05Error, match="unexpected entry"):
        run_fast(flow, out, resume=True)


def test_resume_refuses_a_different_schema(flow: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "out"
    shutil.copytree(flow["root"] / "shards", out)
    with pytest.raises(C05Error, match="unexpected file set|different index schema"):
        run_fast(flow, out, resume=True, index_schema=INDEX_SCHEMA_V1)


# -- freeze ---------------------------------------------------------------------------------


@pytest.mark.parametrize("shards", ["shards", "reference"])
def test_freeze_fast_is_byte_identical_to_reference(
    flow: dict[str, Any], tmp_path: Path, shards: str
) -> None:
    root = flow["root"]
    source = root / "shards" if shards == "shards" else flow["reference"]
    common = [*flow["common"], "--selection", str(root / "selection"), "--shards", str(source)]
    sign = ["--issuer", ISSUER, "--key-env", KEY_ENV]
    assert operator(["freeze-reference", *common, "--output", str(tmp_path / "ref"), *sign]) == 0
    fast = ["--workers", "2", "--no-progress"]
    assert operator(["freeze", *common, "--output", str(tmp_path / "fast"), *fast, *sign]) == 0
    assert (tmp_path / "ref/freeze.json").read_bytes() == (
        tmp_path / "fast/freeze.json"
    ).read_bytes()
    ref = load((tmp_path / "ref/training-data.json").read_bytes())
    new = load((tmp_path / "fast/training-data.json").read_bytes())
    assert ref.pop("c05_freeze") != new.pop("c05_freeze") and ref == new
    for command in ("verify-freeze", "verify-freeze-reference"):
        extra = fast if command == "verify-freeze" else []
        freeze = str(tmp_path / "fast/freeze.json")
        assert (
            operator([command, "--c05-proof", str(root / "proof.json"), "--freeze", freeze, *extra])
            == 0
        )
    data = str(tmp_path / "fast/training-data.json")
    assert operator(["verify-training-freeze", "--training-data", data]) == 0


def test_verify_freeze_fast_detects_changed_shard_bytes(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.exclusion.freezefast import freeze_fast, verify_freeze_fast

    root = flow["root"]
    shards = tmp_path / "shards"
    shutil.copytree(root / "shards", shards)
    envelope = freeze_fast(
        root / "proof.json",
        root / "selection",
        shards,
        root / "tokenizer",
        tmp_path / "freeze",
        ISSUER,
        KEY.encode(),
        workers=1,
        inline=True,
    )
    assert (
        verify_freeze_fast(
            tmp_path / "freeze/freeze.json", root / "proof.json", workers=1, inline=True
        )["digest"]
        == envelope["digest"]
    )
    index = shards / "simple_stories" / "offsets.jsonl"
    lines = index.read_bytes().splitlines(keepends=True)
    record = json.loads(lines[0])
    record["c05_selected_valid_targets"] -= 1
    lines[0] = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    index.write_bytes(b"".join(lines))
    with pytest.raises((ValueError, C05Error)):
        verify_freeze_fast(
            tmp_path / "freeze/freeze.json", root / "proof.json", workers=1, inline=True
        )
    with pytest.raises((ValueError, C05Error)):
        freeze_fast(
            root / "proof.json",
            root / "selection",
            shards,
            root / "tokenizer",
            tmp_path / "freeze2",
            ISSUER,
            KEY.encode(),
            workers=1,
            inline=True,
        )


def test_freeze_fast_refuses_swapped_or_extra_shards(flow: dict[str, Any], tmp_path: Path) -> None:
    from xlm.data.exclusion.freezefast import freeze_fast

    root = flow["root"]
    shards = tmp_path / "shards"
    shutil.copytree(root / "shards", shards)
    names = components(shards)
    (shards / names[0]).rename(shards / "tmp")
    (shards / names[1]).rename(shards / names[0])
    (shards / "tmp").rename(shards / names[1])
    with pytest.raises((ValueError, C05Error)):
        freeze_fast(
            root / "proof.json",
            root / "selection",
            shards,
            root / "tokenizer",
            tmp_path / "freeze",
            ISSUER,
            KEY.encode(),
            workers=1,
            inline=True,
        )
    (shards / "extra").mkdir()
    with pytest.raises(C05Error, match="shard set differs"):
        freeze_fast(
            root / "proof.json",
            root / "selection",
            shards,
            root / "tokenizer",
            tmp_path / "freeze",
            ISSUER,
            KEY.encode(),
            workers=1,
            inline=True,
        )


def test_progress_is_content_free(flow: dict[str, Any], tmp_path: Path) -> None:
    import io

    from xlm.data.exclusion.progress import RunProgress

    stream = io.StringIO()
    run_fast(
        flow,
        tmp_path / "out",
        progress=RunProgress(interval=0.001, stream=stream, label="TOKENIZE"),
    )
    text = stream.getvalue()
    assert "[TOKENIZE] SOURCE TOKENIZE" in text and "ETA" in text and "RSS" in text
    root = flow["root"]
    for record in TokenShardReader(root / "shards" / "simple_stories").iter_document_offsets():
        assert record["doc_id"] not in text
    assert str(root) not in text and str(tmp_path) not in text
