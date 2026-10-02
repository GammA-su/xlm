"""CLI commands for tokenizer operations complying with C06."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.exclusion.gates import screened_documents
from xlm.data.exclusion.transport import open_gate
from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.tokenizers.base import BaseTokenizer
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.tokenizers.byte import ByteTokenizer

app = typer.Typer(help="Tokenizer management, training, and verification commands.")


def _resolve_target_dir(target_str: str) -> Path:
    """Resolve a target string to a local directory or published artifact path."""
    p = Path(target_str)
    if p.is_dir() or p.is_file():
        return p.resolve()

    paths = ArtifactPaths.from_env()
    for kind in ("tokenizers", "clean", "shards", "raw"):
        candidate = paths.root / kind / target_str
        if candidate.is_dir():
            return candidate.resolve()

    raise FileNotFoundError(
        f"Target '{target_str}' is neither an existing directory nor an artifact ID in {paths.root}"
    )


def _load_tokenizer(target_dir: Path) -> BaseTokenizer:
    """Load tokenizer from directory detecting manifest type."""
    manifest_p = target_dir / "tokenizer_manifest.json"
    if not manifest_p.is_file():
        raise FileNotFoundError(f"Missing tokenizer_manifest.json in {target_dir}")

    data = json.loads(manifest_p.read_text(encoding="utf-8"))
    t_type = data.get("type", "bpe")
    if t_type == "byte_fixture":
        return ByteTokenizer.load(target_dir)
    elif t_type == "bpe":
        return ByteLevelBPETokenizer.load(target_dir)
    else:
        raise ValueError(f"Unknown tokenizer type '{t_type}' in manifest")


@app.command("train")
def train(
    data_path: Annotated[
        str,
        typer.Option("--data-path", "-d", help="Path to canonical dataset or artifact ID."),
    ],
    vocab_size: Annotated[
        int,
        typer.Option("--vocab-size", "-v", help="Target vocabulary size (minimum 260)."),
    ] = 32768,
    tok_type: Annotated[
        str,
        typer.Option("--type", "-t", help="Tokenizer type: 'bpe' or 'byte'."),
    ] = "bpe",
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Directory where tokenizer should be saved."),
    ] = None,
    publish: Annotated[
        bool,
        typer.Option("--publish/--no-publish", help="Publish tokenizer as an immutable artifact."),
    ] = False,
    is_production: Annotated[
        bool,
        typer.Option("--is-production/--no-is-production", help="Certify as production baseline."),
    ] = False,
    c05_proof: Annotated[
        Path | None, typer.Option("--c05-proof", help="Verified C05 proof specification.")
    ] = None,
) -> None:
    """Train a BPE or Byte tokenizer on canonical training documents."""
    resolved_data_dir = _resolve_target_dir(data_path)
    jsonl_file = (
        resolved_data_dir / "documents.jsonl" if resolved_data_dir.is_dir() else Path(data_path)
    )
    if not jsonl_file.is_file():
        jsonl_file = Path(data_path)

    if not jsonl_file.is_file():
        typer.echo(f"Error: Canonical documents JSONL file not found at {jsonl_file}", err=True)
        raise typer.Exit(code=1)

    # Read and explicitly select ONLY train documents
    total_count = train_count = 0
    for doc in CanonicalDatasetReader.read_jsonl(jsonl_file):
        total_count += 1
        train_count += doc.split == "train"

    if not train_count:
        typer.echo("Error: No documents with split=='train' found in dataset.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Loaded {train_count} training documents (filtered from {total_count} total).")

    staging_dir = output_dir or (Path(".staging") / f"tokenizer_{tok_type}_{vocab_size}")
    staging_dir.mkdir(parents=True, exist_ok=True)

    if tok_type == "byte":
        with open_gate(c05_proof) as gate:
            for _ in screened_documents(
                (d for d in CanonicalDatasetReader.read_jsonl(jsonl_file) if d.split == "train"),
                gate,
                required=is_production,
            ):
                pass
        tokenizer: BaseTokenizer = ByteTokenizer()
        tokenizer.save(staging_dir)
    elif tok_type == "bpe":
        try:
            with open_gate(c05_proof) as gate:
                tokenizer = ByteLevelBPETokenizer.train_from_documents(
                    documents=(
                        d
                        for d in CanonicalDatasetReader.read_jsonl(jsonl_file)
                        if d.split == "train"
                    ),
                    target_vocab_size=vocab_size,
                    is_production_baseline=is_production,
                    c05_gate=gate,
                )
                tokenizer.save(staging_dir)
                if gate is not None:
                    from xlm.data.acquisition.source_run import write_once

                    write_once(
                        staging_dir / "c05-binding.json",
                        {
                            "plan_digest": gate.plan_digest,
                            "completion_digest": gate.receipt_digest,
                            "tokenizer_fingerprint": tokenizer.fingerprint,
                        },
                    )
        except Exception as e:
            typer.echo(f"Error training BPE tokenizer: {e}", err=True)
            raise typer.Exit(code=1) from e
    else:
        typer.echo(f"Error: Unknown tokenizer type '{tok_type}'", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Tokenizer ({tok_type}) created successfully.")
    typer.echo(f"Target vocab size: {tokenizer.vocab_size}")
    typer.echo(f"Actual vocab size: {tokenizer.actual_vocab_size}")
    typer.echo(f"Production baseline: {tokenizer.is_production_baseline}")
    typer.echo(f"Fingerprint: {tokenizer.fingerprint[:16]}...")
    typer.echo(f"Saved to: {staging_dir}")

    if publish:
        paths = ArtifactPaths.from_env()
        store = ArtifactStore(paths)
        artifact_id = f"tokenizer_{tok_type}_{tokenizer.actual_vocab_size}"
        meta = {
            "type": tok_type,
            "target_vocab_size": tokenizer.vocab_size,
            "actual_vocab_size": tokenizer.actual_vocab_size,
            "is_production_baseline": tokenizer.is_production_baseline,
            "fingerprint": tokenizer.fingerprint,
            "input_lineage": data_path,
        }
        files_map: dict[str, Path] = {
            "tokenizer_manifest.json": staging_dir / "tokenizer_manifest.json"
        }
        tok_json = staging_dir / "tokenizer.json"
        if tok_json.is_file():
            files_map["tokenizer.json"] = tok_json
        binding_file = staging_dir / "c05-binding.json"
        if binding_file.is_file():
            files_map[binding_file.name] = binding_file

        published_dir = store.publish_artifact(
            artifact_id=artifact_id,
            kind="tokenizers",
            files=files_map,
            producer_code_hash=compute_sha256("xlm.cli.tokenizer_cmd:train")[:16],
            dependency_hash=compute_sha256("uv.lock")[:16],
            resolved_config_hash=tokenizer.fingerprint,
            metadata=meta,
        )
        typer.echo(f"Published immutable artifact to: {published_dir}")


@app.command("count-exact")
def count_exact(
    data_path: Annotated[Path, typer.Option("--data-path")],
    tokenizer_dir: Annotated[Path, typer.Option("--tokenizer")],
    c05_proof: Annotated[Path, typer.Option("--c05-proof")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Count unique exact screened training records with a frozen tokenizer."""
    from xlm.data.acquisition.source_run import write_once
    from xlm.data.exclusion.gates import count_exact_tokens

    try:
        with open_gate(c05_proof) as gate:
            if gate is None:
                raise ValueError("C05 proof missing")
            result = count_exact_tokens(
                CanonicalDatasetReader.read_jsonl(data_path), _load_tokenizer(tokenizer_dir), gate
            )
            write_once(output, result)
        typer.echo(json.dumps(result))
    except (ValueError, OSError) as exc:
        typer.echo(f"Exact count refused: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc


@app.command("inspect")
def inspect(
    target: Annotated[str, typer.Argument(help="Artifact ID or directory path of tokenizer.")],
) -> None:
    """Inspect tokenizer manifest, vocabulary statistics, and special token configuration."""
    try:
        target_dir = _resolve_target_dir(target)
        manifest_p = target_dir / "tokenizer_manifest.json"
        manifest_data = json.loads(manifest_p.read_text(encoding="utf-8"))
    except Exception as e:
        typer.echo(f"Error inspecting tokenizer: {e}", err=True)
        raise typer.Exit(code=1) from e

    target_v = manifest_data.get("target_vocab_size", manifest_data.get("vocab_size"))
    typer.echo("--- Tokenizer Manifest ---")
    typer.echo(f"Location: {target_dir}")
    typer.echo(f"Type: {manifest_data.get('type')}")
    typer.echo(f"Version: {manifest_data.get('version')}")
    typer.echo(f"Target Vocabulary Size: {target_v}")
    typer.echo(f"Actual Vocabulary Size: {manifest_data.get('actual_vocab_size')}")
    typer.echo(f"Is Production Baseline: {manifest_data.get('is_production_baseline')}")
    typer.echo(f"Special Tokens: {manifest_data.get('special_tokens')}")
    typer.echo(f"Training Input Hash: {manifest_data.get('training_input_hash')}")
    typer.echo(f"Fingerprint: {manifest_data.get('fingerprint')}")


@app.command("encode")
def encode(
    target: Annotated[str, typer.Argument(help="Artifact ID or directory path of tokenizer.")],
    text: Annotated[str, typer.Argument(help="Input text string to encode.")],
    add_special_tokens: Annotated[
        bool,
        typer.Option(
            "--add-special-tokens/--no-special-tokens", help="Frame sequence with BOS and EOS."
        ),
    ] = False,
) -> None:
    """Encode input text, displaying token IDs, byte spans, and round-trip fidelity."""
    try:
        target_dir = _resolve_target_dir(target)
        tokenizer = _load_tokenizer(target_dir)
    except Exception as e:
        typer.echo(f"Error loading tokenizer: {e}", err=True)
        raise typer.Exit(code=1) from e

    ids, offsets = tokenizer.encode_with_offsets(text, add_special_tokens=add_special_tokens)
    decoded = tokenizer.decode(ids, skip_special_tokens=False)

    # Check for reserved control token IDs
    control_ids_in_output = tokenizer.special_token_ids & set(ids)

    typer.echo(f"Input text: {repr(text)}")
    typer.echo(f"Token count: {len(ids)}")
    typer.echo(f"Token IDs: {ids}")
    typer.echo(f"Byte spans: {offsets}")
    typer.echo(f"Decoded round-trip: {repr(decoded)}")
    typer.echo(f"Control IDs emitted: {sorted(control_ids_in_output)}")


@app.command("verify")
def verify(
    target: Annotated[str, typer.Argument(help="Artifact ID or directory path of tokenizer.")],
) -> None:
    """Run automated verification suite on tokenizer across Unicode, code, and emoji."""
    try:
        target_dir = _resolve_target_dir(target)
        tokenizer = _load_tokenizer(target_dir)
    except Exception as e:
        typer.echo(f"Error loading tokenizer: {e}", err=True)
        raise typer.Exit(code=1) from e

    test_cases = [
        ("ASCII Prose", "The quick brown fox jumps over the lazy dog."),
        ("Non-English Names", "François Müller, José García, Märt Raud, 李白, Søren Kierkegaard."),
        ("Python Code", "def foo(x: int) -> int:\n    return x + 42\n"),
        (
            "Mathematical Equations",
            "Euler's identity: e^{i\\pi} + 1 = 0; \\sum_{k=1}^n k = n(n+1)/2.",
        ),
        ("Emoji Sequences", "Exploring frontiers! 🚀 🎉 🤖 ✨ 👨‍👩‍👧‍👦"),
        (
            "Literal Special Tokens",
            "Literal text contains <eos> and <pad> and <bos> and <unk> verbatim.",
        ),
        ("Adjacent Special Tokens", "<eos><bos><pad><unk>"),
    ]

    all_passed = True
    typer.echo(f"Verifying tokenizer: {target_dir}")

    for name, sample_text in test_cases:
        clean_text = canonical_normalize(sample_text)
        canonical_bytes = clean_text.encode("utf-8")

        # 1. Reversibility check without special tokens
        ids, offsets = tokenizer.encode_with_offsets(clean_text, add_special_tokens=False)
        decoded = tokenizer.decode(ids, skip_special_tokens=False)

        if decoded != clean_text:
            typer.echo(
                f"FAIL [{name}]: Round-trip mismatch. Expected {repr(clean_text)}, "
                f"got {repr(decoded)}",
                err=True,
            )
            all_passed = False
            continue

        # 2. Literal special-token protection check
        # When add_special_tokens=False, NO reserved control IDs (0..3) must be emitted
        control_hits = tokenizer.special_token_ids & set(ids)
        if control_hits:
            typer.echo(
                f"FAIL [{name}]: Emitted reserved control IDs {control_hits} for literal text!",
                err=True,
            )
            all_passed = False
            continue

        # 3. True byte span check
        prev_end = 0
        for start, end in offsets:
            if start != prev_end:
                typer.echo(f"FAIL [{name}]: Offset gap or overlap at [{start}, {end})", err=True)
                all_passed = False
                break
            prev_end = end

        if prev_end != len(canonical_bytes):
            typer.echo(
                f"FAIL [{name}]: Total byte span length {prev_end} != {len(canonical_bytes)}",
                err=True,
            )
            all_passed = False
            continue

        typer.echo(f"PASS [{name}]: {len(ids)} tokens, byte coverage 100%, 0 control IDs emitted.")

    # 4. Out-of-bounds validation check
    try:
        tokenizer.decode([-1])
        typer.echo("FAIL: Out-of-bounds token ID -1 was not rejected!", err=True)
        all_passed = False
    except ValueError:
        typer.echo("PASS [Boundary]: Negative token ID -1 correctly rejected.")

    try:
        tokenizer.decode([9999999])
        typer.echo("FAIL: Out-of-bounds token ID 9999999 was not rejected!", err=True)
        all_passed = False
    except ValueError:
        typer.echo("PASS [Boundary]: Out-of-range token ID correctly rejected.")

    if not all_passed:
        typer.echo("Verification FAILED for one or more checks.", err=True)
        raise typer.Exit(code=1)

    typer.echo("All tokenizer verification checks PASSED successfully.")
