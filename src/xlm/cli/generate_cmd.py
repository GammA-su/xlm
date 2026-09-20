"""CLI command for autoregressive text generation complying with Amendment 6."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Annotated

import typer


def generate_command(
    checkpoint: Annotated[
        Path,
        typer.Argument(
            help="Path to checkpoint directory or artifact ID.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
        ),
    ],
    prompt: Annotated[
        str,
        typer.Option("--prompt", "-p", help="Text prompt to complete."),
    ] = "",
    prompt_file: Annotated[
        Path | None,
        typer.Option("--prompt-file", help="Path to text file containing prompt."),
    ] = None,
    tokenizer_path: Annotated[
        str | None,
        typer.Option("--tokenizer", help="Path to tokenizer directory or artifact ID."),
    ] = None,
    max_new_tokens: Annotated[
        int,
        typer.Option("--max-new-tokens", "-n", help="Maximum tokens to generate."),
    ] = 32,
    temperature: Annotated[
        float,
        typer.Option("--temperature", "-t", help="Sampling temperature."),
    ] = 1.0,
    top_k: Annotated[
        int,
        typer.Option("--top-k", "-k", help="Top-k filtering threshold."),
    ] = 0,
    top_p: Annotated[
        float,
        typer.Option("--top-p", help="Nucleus top-p threshold."),
    ] = 1.0,
    repetition_penalty: Annotated[
        float,
        typer.Option("--repetition-penalty", help="Repetition penalty."),
    ] = 1.0,
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Enable stochastic sampling."),
    ] = False,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Random seed for generation."),
    ] = None,
    device: Annotated[
        str,
        typer.Option("--device", help="Device ('cpu' or 'cuda')."),
    ] = "cpu",
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Output raw generation result JSON."),
    ] = False,
) -> None:
    """Generate text completion from a model checkpoint."""
    if importlib.util.find_spec("torch") is None:
        typer.echo(
            "Error: PyTorch is required for generation. "
            "Run with uv run --extra cpu/cuda xlm generate",
            err=True,
        )
        raise typer.Exit(code=1)

    from xlm.inference.generation import GenerationConfig, TextGenerator
    from xlm.models.serialization import load_model_for_inference

    # 1. Resolve prompt text
    input_text = prompt
    if prompt_file is not None:
        if not prompt_file.is_file():
            typer.echo(f"Error: Prompt file not found: {prompt_file}", err=True)
            raise typer.Exit(code=1)
        input_text = prompt_file.read_text(encoding="utf-8")

    # 2. Load model for inference
    model = load_model_for_inference(checkpoint, device=device)

    # 3. Load or instantiate tokenizer
    from xlm.tokenizers.loading import load_inference_tokenizer

    tok = load_inference_tokenizer(checkpoint, tokenizer_path)

    # 4. Configure generator
    gen_config = GenerationConfig(
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        do_sample=sample,
        top_k=top_k,
        top_p=top_p,
        repetition_penalty=repetition_penalty,
        seed=seed,
    )

    generator = TextGenerator(model=model, tokenizer=tok, device=device)
    res = generator.generate(input_text, config=gen_config)

    if output_json:
        payload = {
            "prompt": res.prompt,
            "generated_text": res.generated_text,
            "full_text": res.full_text,
            "prompt_token_ids": res.prompt_token_ids,
            "generated_token_ids": res.generated_token_ids,
            "finish_reason": res.finish_reason,
            "diagnostics": res.diagnostics,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("=" * 60)
    typer.echo("XLM Text Generation Result")
    typer.echo(
        f"Finish Reason: {res.finish_reason} | Generated Tokens: {len(res.generated_token_ids)}"
    )
    typer.echo("=" * 60)
    typer.echo(f"\n[Prompt]:\n{res.prompt}")
    typer.echo(f"\n[Completion]:\n{res.generated_text}\n")
    typer.echo("=" * 60)


def session_command(
    checkpoint: Annotated[
        Path,
        typer.Argument(
            help="Path to checkpoint directory, export bundle or artifact ID.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
        ),
    ],
    prompts: Annotated[
        str,
        typer.Option("--prompts", help="Prompts separated by '\\n---\\n'."),
    ] = "",
    prompt_file: Annotated[
        Path | None,
        typer.Option("--prompt-file", help="File with prompts separated by '\\n---\\n'."),
    ] = None,
    tokenizer_path: Annotated[
        str | None,
        typer.Option("--tokenizer", help="Path to tokenizer directory or artifact ID."),
    ] = None,
    max_new_tokens: Annotated[
        int,
        typer.Option("--max-new-tokens", "-n", help="Maximum tokens per completion."),
    ] = 32,
    stop: Annotated[
        list[str] | None,
        typer.Option("--stop", help="Stop string (repeatable)."),
    ] = None,
    session_seed: Annotated[
        int,
        typer.Option("--session-seed", help="Session seed; replays match exactly."),
    ] = 20260919,
    device: Annotated[
        str,
        typer.Option("--device", help="Device ('cpu' or 'cuda')."),
    ] = "cpu",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the session transcript JSON."),
    ] = None,
) -> None:
    """Run a bounded completion session. No chat template is ever attached."""
    if importlib.util.find_spec("torch") is None:
        typer.echo(
            "Error: PyTorch is required for generation. "
            "Run with uv run --extra cpu/cuda xlm generate-session",
            err=True,
        )
        raise typer.Exit(code=1)

    from xlm.inference.generation import TextGenerator
    from xlm.inference.session import run_session
    from xlm.models.serialization import load_model_for_inference

    if prompt_file is not None:
        if not prompt_file.is_file():
            typer.echo(f"Error: Prompt file not found: {prompt_file}", err=True)
            raise typer.Exit(code=1)
        raw_prompts = prompt_file.read_text(encoding="utf-8")
    else:
        raw_prompts = prompts
    prompt_list = [p for p in raw_prompts.split("\n---\n") if p.strip()]
    if not prompt_list:
        typer.echo("Error: no prompts supplied.", err=True)
        raise typer.Exit(code=1)

    model = load_model_for_inference(checkpoint, device=device)

    from xlm.tokenizers.loading import load_inference_tokenizer

    tok = load_inference_tokenizer(checkpoint, tokenizer_path)

    generator = TextGenerator(model=model, tokenizer=tok, device=device)
    try:
        session = run_session(
            generator,
            prompt_list,
            session_seed=session_seed,
            max_new_tokens=max_new_tokens,
            stop_strings=list(stop or []),
        )
    except Exception as exc:
        typer.echo(f"Error: session failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output is not None:
        session.save(output)
        typer.echo(f"Session transcript ({len(session.turns)} turns) written to {output}")
    for index, turn in enumerate(session.turns):
        typer.echo(f"--- turn {index} [{turn.finish_reason}] ---")
        typer.echo(turn.completion)
