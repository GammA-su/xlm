"""Bounded, text-free offline replay of the already acquired 256-row probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psutil

from xlm.data.adapters.essential_web_selector import (
    ADMITTED_COMPONENTS,
    FrozenEssentialWebSelector,
    selector_identity,
)
from xlm.data.adapters.mix01_adapters import EssentialWebAdapter, EssentialWebSelectedAdapter

SHAPES = ("absent", "null", "empty", "whitespace", "nonempty", "nonstring")
EXPECTED = dict(rejected=223, essential_prose=26, essential_practical=5, unassigned=2)


def no_network(event: str, args: tuple[Any, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("offline replay denies network")


def shape(labels: dict[str, Any], level: str) -> str:
    if level not in labels:
        return "absent"
    value = labels[level]
    if value is None:
        return "null"
    if not isinstance(value, str):
        return "nonstring"
    if value == "":
        return "empty"
    return "nonempty" if value.strip() else "whitespace"


def main() -> None:
    sys.addaudithook(no_network)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refuse to replace replay evidence")
    started = time.perf_counter()
    if args.raw.stat().st_size > 256 * 1024**2:
        raise ValueError("raw byte cap exceeded")
    shapes = {
        scope: {
            level: Counter(dict.fromkeys(SHAPES, 0)) for level in ("level_1", "level_2", "level_3")
        }
        for scope in ("all", "selected")
    }
    frozen = FrozenEssentialWebSelector.load()
    adapters = {v: EssentialWebSelectedAdapter(v) for v in ADMITTED_COMPONENTS}
    passes: dict[str, Counter[str]] = {
        v: Counter(
            dict.fromkeys(
                (
                    "accepted",
                    "policy_rejected",
                    "unassigned",
                    "other_component",
                    "malformed",
                    "other_exception",
                ),
                0,
            )
        )
        for v in ADMITTED_COMPONENTS
    }
    categories = dict(
        EssentialWebSelectorRejectedError="policy_rejected",
        EssentialWebSelectorUnassignedError="unassigned",
        EssentialWebSelectorOtherComponentError="other_component",
        EssentialWebMalformedRowError="malformed",
    )
    finals: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    selected_errors: Counter[str] = Counter()
    rendered: Counter[str] = Counter()
    validity_failures = base_ok = n = 0
    digest = hashlib.sha256()
    separators: Counter[str] = Counter()
    with args.raw.open("rb") as stream:
        while raw := stream.readline(1024**2 + 1):
            if len(raw) > 1024**2 or n >= 256:
                raise ValueError("row byte/count cap exceeded")
            digest.update(raw)
            record = json.loads(raw)
            separators.update(
                {f"U+{ord(c):04X}": record["text"].count(c) for c in ("\u0085", "\u2028")}
            )
            decision = frozen.decide(record)
            finals[decision.final] += 1
            validity_failures += decision.stage == "validity"
            labels = record["eai_taxonomy"]["free_decimal_correspondence"]["primary"]["labels"]
            for level in shapes["all"]:
                category = shape(labels, level)
                shapes["all"][level][category] += 1
                if decision.admitted:
                    shapes["selected"][level][category] += 1
            loc = record["_xlm_acquisition"]
            kwargs = dict(
                source_file=loc["source_file"],
                source_row=loc["row_index"],
                source_revision=loc["revision"],
            )
            try:
                doc = EssentialWebAdapter("essential_prose").adapt(record, **kwargs)
                if doc.text != record["text"]:
                    raise ValueError("text changed")
                base_ok += 1
                if decision.admitted:
                    rendered[decision.final] += 1
            except Exception as exc:
                # Renderer field errors name fields/types, never document text.
                reason = type(exc).__name__
                if reason == "MissingFieldError":
                    reason += ": " + str(exc)
                errors[reason] += 1
                if decision.admitted:
                    selected_errors[decision.final + ": " + reason] += 1
            for view, adapter in adapters.items():
                try:
                    doc = adapter.adapt(record, **kwargs)
                    if doc.text != record["text"]:
                        raise ValueError("text changed")
                    passes[view]["accepted"] += 1
                except Exception as exc:
                    passes[view][categories.get(type(exc).__name__, "other_exception")] += 1
            n += 1
    if n != 256 or dict(finals) != EXPECTED or validity_failures:
        raise ValueError("STOP: frozen selector totals or row count changed")
    result = dict(
        rows=n,
        raw_bytes=args.raw.stat().st_size,
        raw_sha256=digest.hexdigest(),
        shapes=shapes,
        selector_counts={**finals, "essential_science": 0},
        selector_identity=selector_identity(),
        validity_failures=validity_failures,
        selected_rendered=dict(rendered),
        base_rendered=base_ok,
        base_errors=errors,
        selected_errors=selected_errors,
        passes=passes,
        unicode_separators=separators,
        wall_seconds=time.perf_counter() - started,
        process_memory=psutil.Process().memory_info()._asdict(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, indent=2) + "\n")
    temporary.rename(args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
