"""Bounded text-completion sessions with deterministic replay (P20, A35).

A session is a completion-model interaction: prompts in, completions out, no
chat template attached (a template would change benchmark behavior). State
resets between prompts, and each prompt derives its own RNG seed from the
session seed, so replaying a transcript file reproduces every completion
exactly. Every prompt honors max-new-token, stop-string and context-limit
handling; the full transcript (prompts, completions, finish reasons, token
counts) is saved for the record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.inference.generation import GenerationConfig, TextGenerator

SESSION_VERSION = "1"


class SessionError(RuntimeError):
    """Raised when a session request violates its bounds."""


@dataclass
class SessionTurn:
    """One prompt and its completion."""

    prompt: str
    completion: str
    generated_token_ids: list[int]
    finish_reason: str
    prompt_seed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompletionSession:
    """A bounded, replayable completion session."""

    session_version: str
    session_seed: int
    max_new_tokens: int
    stop_strings: list[str] = field(default_factory=list)
    turns: list[SessionTurn] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_version": self.session_version,
            "session_seed": self.session_seed,
            "max_new_tokens": self.max_new_tokens,
            "stop_strings": self.stop_strings,
            "turns": [t.to_dict() for t in self.turns],
        }

    def save(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return target

    @classmethod
    def load(cls, path: Path | str) -> CompletionSession:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            session_version=str(data.get("session_version", SESSION_VERSION)),
            session_seed=int(data["session_seed"]),
            max_new_tokens=int(data["max_new_tokens"]),
            stop_strings=list(data.get("stop_strings", [])),
            turns=[SessionTurn(**t) for t in data.get("turns", [])],
        )


def derive_prompt_seed(session_seed: int, prompt_index: int, prompt: str) -> int:
    """Deterministically derive a per-prompt seed so replays match exactly."""
    digest = hashlib.sha256(f"{session_seed}:{prompt_index}:{prompt}".encode()).hexdigest()
    return int(digest[:16], 16)


def run_session(
    generator: TextGenerator,
    prompts: list[str],
    session_seed: int = 20260919,
    max_new_tokens: int = 64,
    stop_strings: list[str] | None = None,
    temperature: float = 0.0,
) -> CompletionSession:
    """Run a bounded completion session with per-prompt state reset.

    The generator is stateless across calls by construction; the session
    additionally reseeds sampling per prompt so identical transcripts replay
    byte-for-byte. Temperature 0 (greedy) is the default; sampling callers must
    pass an explicit temperature.
    """
    if not prompts:
        raise SessionError("a session needs at least one prompt")
    if max_new_tokens <= 0:
        raise SessionError(f"max_new_tokens must be positive, got {max_new_tokens}")
    stops = list(stop_strings or [])
    session = CompletionSession(
        session_version=SESSION_VERSION,
        session_seed=session_seed,
        max_new_tokens=max_new_tokens,
        stop_strings=stops,
    )
    for index, prompt in enumerate(prompts):
        config = GenerationConfig(
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=temperature > 0.0,
            stop_tokens=None,
            seed=derive_prompt_seed(session_seed, index, prompt),
        )
        result = generator.generate(prompt, config)
        completion = result.generated_text
        finish_reason = result.finish_reason
        for stop in stops:
            if stop and stop in completion:
                completion = completion.split(stop, 1)[0]
                finish_reason = f"{finish_reason}+until"
                break
        session.turns.append(
            SessionTurn(
                prompt=prompt,
                completion=completion,
                generated_token_ids=list(result.generated_token_ids),
                finish_reason=finish_reason,
                prompt_seed=config.seed or 0,
            )
        )
    return session
