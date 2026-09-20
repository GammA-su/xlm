"""Author the synthetic evaluation-input fixtures used by the D04/D05 tests.

Records follow the OFFICIAL benchmark record schemas so the pinned installed
task definitions apply unchanged - that is the point of the D04 repair - but
every question, sentence, choice and label is invented. See README.md.

Run:

    uv run --offline --locked --extra cpu --extra eval \
        python fixtures/eval/inputs/author_inputs.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT / "src") not in sys.path:  # pragma: no cover - script convenience
    sys.path.insert(0, str(REPO_ROOT / "src"))

#: Shapes imitated. Recorded so a manifest is self-describing; NOT a claim that
#: the content below was obtained from these repositories.
SHAPES = {
    "arc_easy": ("allenai/ai2_arc", "210d026faf9955653af8916fad021475a3f00453", "ARC-Easy"),
    "hellaswag": ("Rowan/hellaswag", "218ec52e09a7e7462a5400043bb9a69a41d06b76", None),
    "piqa": ("baber/piqa", "142f6d7367fd9877f0fb3b5734ea6a545f54cdd1", None),
    "blimp": ("nyu-mll/blimp", "877fba0801ffb7cbd8c39c1ff314a46f053f6036", None),
}

BLIMP_SUBDATASETS = ("blimp_adjunct_island", "blimp_anaphor_gender_agreement")


def _locator(task: str, split: str, row: int) -> str:
    """Stable source locator for records whose schema has no intrinsic id.

    Fixed at selection time from the source and row position within the
    selected split. It is never recomputed from a result position.
    """
    return f"syn_{task}/{split}/{row:04d}"


def arc_records(count: int, offset: int = 0) -> list[dict[str, Any]]:
    seeds = [
        ("What colour is the fixture sky at noon?", ["blue", "plaid"], "A"),
        ("How many moons orbit the fixture planet?", ["two", "nine"], "A"),
        ("Which fixture tool measures invented heat?", ["a ruler", "a thermometer"], "B"),
        ("What do fixture plants need in order to grow?", ["light", "gravel"], "A"),
        ("What state is fixture water in at minus ten?", ["solid", "vapour"], "A"),
        ("What powers the fixture windmill on the hill?", ["moonlight", "wind"], "B"),
        ("Which fixture animal is described as nocturnal?", ["the owl", "the sparrow"], "A"),
        ("What happens to fixture ice left in the sun?", ["it melts", "it hardens"], "A"),
    ]
    return [
        {
            "id": f"syn_arc_{offset + i:04d}",
            "question": seeds[(offset + i) % len(seeds)][0],
            "choices": {
                "text": list(seeds[(offset + i) % len(seeds)][1]),
                "label": ["A", "B"],
            },
            "answerKey": seeds[(offset + i) % len(seeds)][2],
        }
        for i in range(count)
    ]


def hellaswag_records(count: int, offset: int = 0) -> list[dict[str, Any]]:
    seeds = [
        (
            "Fixture cooking",
            "A fixture cook opens the oven",
            "and then",
            ["removes the tray.", "paints a wall."],
            "0",
        ),
        (
            "Fixture running",
            "The fixture runner ties both laces",
            "and then",
            ["starts running.", "eats the laces."],
            "0",
        ),
        (
            "Fixture cycling",
            "A fixture cyclist stops at the light",
            "and then",
            ["waits for green.", "sells the bike."],
            "0",
        ),
        (
            "Fixture painting",
            "The fixture painter dips the brush",
            "and then",
            ["drinks the paint.", "paints the fence."],
            "1",
        ),
        (
            "Fixture reading",
            "A fixture reader opens the book",
            "and then",
            ["turns a page.", "boils the book."],
            "0",
        ),
        (
            "Fixture gardening",
            "The fixture gardener waters the seed",
            "and then",
            ["waits for a sprout.", "mails the seed."],
            "0",
        ),
        (
            "Fixture sailing",
            "A fixture sailor raises the sail",
            "and then",
            ["catches the wind.", "eats the mast."],
            "0",
        ),
        (
            "Fixture baking",
            "The fixture baker kneads the dough",
            "and then",
            ["files it away.", "lets it rise."],
            "1",
        ),
    ]
    rows = []
    for i in range(count):
        label, ctx_a, ctx_b, endings, gold = seeds[(offset + i) % len(seeds)]
        rows.append(
            {
                "xlm_item_locator": _locator("hellaswag", "train", offset + i),
                "ind": offset + i,
                "activity_label": label,
                "ctx_a": ctx_a,
                "ctx_b": ctx_b,
                "ctx": f"{ctx_a} {ctx_b}",
                "endings": list(endings),
                "source_id": "synthetic_fixture",
                "split": "train",
                "split_type": "indomain",
                "label": gold,
            }
        )
    return rows


def piqa_records(count: int, offset: int = 0) -> list[dict[str, Any]]:
    seeds = [
        ("dry a fixture cup quickly", "use a dry cloth", "use a magnet", 0),
        ("carry hot fixture soup safely", "use bare hands", "use a tray", 1),
        ("open a stuck fixture jar", "grip it with a cloth", "shout at it", 0),
        ("keep fixture bread fresh", "seal the bag", "leave the bag open", 0),
        ("cool a fixture drink", "add warm water", "add ice cubes", 1),
        ("reach a high fixture shelf", "use a step stool", "jump blindly", 0),
        ("clean a dusty fixture screen", "use a soft cloth", "use sandpaper", 0),
        ("stop a fixture door slamming", "wedge it open", "oil the hinge", 0),
    ]
    rows = []
    for i in range(count):
        goal, sol1, sol2, label = seeds[(offset + i) % len(seeds)]
        rows.append(
            {
                "xlm_item_locator": _locator("piqa", "train", offset + i),
                "goal": goal,
                "sol1": sol1,
                "sol2": sol2,
                "label": label,
            }
        )
    return rows


def blimp_records(subdataset: str, count: int, offset: int = 0) -> list[dict[str, Any]]:
    seeds = {
        "blimp_adjunct_island": [
            ("The fixture cats sleep quietly.", "The fixture cats sleeps quietly."),
            ("A fixture dog barks at noon.", "A fixture dog bark at noon."),
            ("These fixture books are new.", "These fixture books is new."),
            ("The fixture child runs home.", "The fixture child run home."),
        ],
        "blimp_anaphor_gender_agreement": [
            ("Nobody has ever seen it.", "Anybody has ever seen it."),
            ("She could not have known.", "She could not have knowed."),
            ("No student ever arrived late.", "Any student ever arrived late."),
            ("He has never eaten there.", "He has never ate there."),
        ],
    }[subdataset]
    rows = []
    for i in range(count):
        good, bad = seeds[(offset + i) % len(seeds)]
        rows.append(
            {
                "xlm_item_locator": _locator(subdataset, "train", offset + i),
                "sentence_good": good,
                "sentence_bad": bad,
                "field": "syntax",
                "linguistics_term": "synthetic_fixture",
                "UID": subdataset.removeprefix("blimp_"),
                "pair_id": offset + i,
                "one_prefix_method": False,
                "two_prefix_method": False,
                "simple_LM_method": True,
            }
        )
    return rows


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write with explicit LF bytes.

    A manifest records the byte digest of every artifact, so these files must be
    byte-identical on every platform. Letting the OS pick the newline would make
    each digest platform-dependent and break verification after a checkout.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(rows, indent=2) + "\n").encode("utf-8"))


def _write_entries(root: Path, doc: dict[str, Any]) -> None:
    """Entry descriptions are YAML-compatible JSON, written as LF bytes."""
    (root / "entries.yaml").write_bytes((json.dumps(doc, indent=2) + "\n").encode("utf-8"))


def _entry(
    task: str,
    leaf: str,
    data_file: str,
    id_field: str,
    label_field: str | None,
    subdataset: str | None = None,
    population: int | None = None,
) -> dict[str, Any]:
    repository, revision, config = SHAPES[task]
    entry: dict[str, Any] = {
        "task": task,
        "leaf_task": leaf,
        "source_repository": repository,
        "source_revision": revision,
        "source_split": "train",
        "record_schema_version": f"{task}.official_shape.v1",
        "adapter_version": "xlm_eval_json.v1",
        "item_id_field": id_field,
        "data_file": data_file,
        "notes": ["AUTHORED SYNTHETIC CONTENT in the official record shape"],
    }
    if config:
        entry["source_config"] = config
    if label_field:
        entry["label_field"] = label_field
    if subdataset:
        entry["subdataset"] = subdataset
    if population is not None:
        entry["source_population_size"] = population
    return entry


def _entries_doc(
    scope_label: str, entries: list[dict[str, Any]], required: list[str]
) -> dict[str, Any]:
    return {
        "scope_label": scope_label,
        "scope_kind": "authored_fixture",
        "exposure_class": "authored_fixture",
        "tier": "search",
        "required_blimp_subdatasets": required,
        "acquisition_receipts": [],
        "notes": [
            "Authored synthetic fixture. Not an official benchmark scope and never "
            "research or promotion evidence.",
        ],
        "entries": entries,
    }


def build_complete(root: Path) -> None:
    """Positive control: four tasks, both BLiMP subdatasets, fully covered."""
    _write(root / "arc_easy.json", arc_records(4))
    _write(root / "hellaswag.json", hellaswag_records(4))
    _write(root / "piqa.json", piqa_records(4))
    for sub in BLIMP_SUBDATASETS:
        _write(root / f"{sub}.json", blimp_records(sub, 3))
    entries = [
        _entry("arc_easy", "arc_easy", "arc_easy.json", "id", "answerKey", population=2251),
        _entry(
            "hellaswag",
            "hellaswag",
            "hellaswag.json",
            "xlm_item_locator",
            "label",
            population=39905,
        ),
        _entry("piqa", "piqa", "piqa.json", "xlm_item_locator", "label", population=16113),
    ] + [
        _entry(
            "blimp", sub, f"{sub}.json", "xlm_item_locator", None, subdataset=sub, population=1000
        )
        for sub in BLIMP_SUBDATASETS
    ]
    doc = _entries_doc("authored fixture suite v1 (complete)", entries, list(BLIMP_SUBDATASETS))
    _write_entries(root, doc)


def build_subset(root: Path) -> None:
    """A correctly declared narrower scope: two tasks, complete within itself."""
    _write(root / "arc_easy.json", arc_records(3))
    _write(root / "piqa.json", piqa_records(3))
    entries = [
        _entry("arc_easy", "arc_easy", "arc_easy.json", "id", "answerKey", population=2251),
        _entry("piqa", "piqa", "piqa.json", "xlm_item_locator", "label", population=16113),
    ]
    doc = _entries_doc("authored fixture two-task subset v1", entries, [])
    _write_entries(root, doc)


def main() -> int:
    for name, builder in (
        ("dev_fixture_v1", build_complete),
        ("dev_fixture_subset", build_subset),
    ):
        root = HERE / name
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        builder(root)
        print(f"wrote {root}")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
