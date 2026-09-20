"""Author benchmark-SHAPED synthetic inputs for the D04/D05 reproduction.

Nothing here is official benchmark content. Every sentence is invented for this
reproduction, every id carries a ``syn_`` marker, and the answer keys are
hand-written so the expected metrics can be computed on paper. The task YAML
files deliberately take the four logical suite names (arc_easy, hellaswag,
piqa, blimp) because the defect under test lives in the four-task index and
coverage code, which keys on exactly those names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HEADER = (
    "# AUTHORED SYNTHETIC FIXTURE - not official benchmark data.\n"
    "# Written for the P23 D04/D05 reproduction; content is invented.\n"
)

MC_YAML = """{header}task: {name}
dataset_path: json
dataset_kwargs:
  data_files:
    train: data/{name}.json
output_type: multiple_choice
training_split: train
validation_split: train
test_split: train
doc_to_text: "Question: {{{{question}}}}\nAnswer:"
doc_to_choice: "{{{{choices}}}}"
doc_to_target: "{{{{answer}}}}"
metric_list:
  - metric: acc
    aggregation: mean
    higher_is_better: true
  - metric: acc_norm
    aggregation: mean
    higher_is_better: true
metadata:
  version: 1.0
"""

PAIR_YAML = """{header}task: {name}
dataset_path: json
dataset_kwargs:
  data_files:
    train: data/{name}.json
output_type: multiple_choice
training_split: train
validation_split: train
test_split: train
doc_to_text: ""
doc_to_choice: "{{{{choices}}}}"
doc_to_target: "{{{{answer}}}}"
metric_list:
  - metric: acc
    aggregation: mean
    higher_is_better: true
  - metric: acc_norm
    aggregation: mean
    higher_is_better: true
metadata:
  version: 1.0
"""

# Six authored items per multiple-choice task. Answers are fixed by hand.
MC_ITEMS = {
    "arc_easy": [
        ("syn_arc_0", "What colour is the synthetic sky in fixture world?", ["blue", "plaid"], 0),
        ("syn_arc_1", "How many moons orbit the fixture planet?", ["two", "nine"], 0),
        ("syn_arc_2", "Which fixture tool measures invented heat?", ["ruler", "thermometer"], 1),
        ("syn_arc_3", "What do fixture plants need to grow?", ["light", "gravel"], 0),
        ("syn_arc_4", "Which fixture state is water at minus ten?", ["solid", "vapour"], 0),
        ("syn_arc_5", "What powers the fixture windmill?", ["moonlight", "wind"], 1),
    ],
    "hellaswag": [
        ("syn_hs_0", "A fixture cook opens the oven and then", ["removes the tray", "paints a wall"], 0),
        ("syn_hs_1", "The fixture runner ties both laces and then", ["starts running", "eats the laces"], 0),
        ("syn_hs_2", "A fixture cyclist stops at the light and then", ["waits for green", "sells the bike"], 0),
        ("syn_hs_3", "The fixture painter dips the brush and then", ["drinks the paint", "paints the fence"], 1),
        ("syn_hs_4", "A fixture reader opens the book and then", ["turns a page", "boils the book"], 0),
        ("syn_hs_5", "The fixture gardener waters the seed and then", ["waits for a sprout", "mails the seed"], 0),
    ],
    "piqa": [
        ("syn_piqa_0", "To dry a fixture cup quickly you should", ["use a cloth", "use a magnet"], 0),
        ("syn_piqa_1", "To carry hot fixture soup you should", ["use bare hands", "use a tray"], 1),
        ("syn_piqa_2", "To open a stuck fixture jar you should", ["grip with a cloth", "shout at it"], 0),
        ("syn_piqa_3", "To keep fixture bread fresh you should", ["seal the bag", "open the bag"], 0),
        ("syn_piqa_4", "To cool a fixture drink you should", ["add warm water", "add ice"], 1),
        ("syn_piqa_5", "To reach a high fixture shelf you should", ["use a step stool", "jump blindly"], 0),
    ],
}

# Two authored BLiMP-shaped subdatasets; the declared scope needs BOTH.
PAIR_ITEMS = {
    "blimp_syn_alpha": [
        ("syn_alpha_0", ["The fixture cats sleep.", "The fixture cats sleeps."], 0),
        ("syn_alpha_1", ["A fixture dog barks.", "A fixture dog bark."], 0),
        ("syn_alpha_2", ["These fixture books are new.", "These fixture books is new."], 0),
        ("syn_alpha_3", ["The fixture child runs.", "The fixture child run."], 0),
    ],
    "blimp_syn_beta": [
        ("syn_beta_0", ["Nobody has ever seen it.", "Anybody has ever seen it."], 0),
        ("syn_beta_1", ["She could not have known.", "She could not have knowed."], 0),
        ("syn_beta_2", ["No student ever arrived.", "Any student ever arrived."], 0),
        ("syn_beta_3", ["He has never eaten there.", "He has never ate there."], 0),
    ],
}

# Single-subdataset stand-in used when the run requests the logical `blimp` leaf.
PAIR_ITEMS["blimp"] = PAIR_ITEMS["blimp_syn_alpha"]


def write_tasks(root: Path) -> Path:
    tasks = root / "tasks"
    data = tasks / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name, rows in MC_ITEMS.items():
        (tasks / f"{name}.yaml").write_text(
            MC_YAML.format(header=HEADER, name=name), encoding="utf-8"
        )
        payload = [
            {"id": i, "question": q, "choices": c, "answer": a} for i, q, c, a in rows
        ]
        (data / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for name, rows in PAIR_ITEMS.items():
        (tasks / f"{name}.yaml").write_text(
            PAIR_YAML.format(header=HEADER, name=name), encoding="utf-8"
        )
        payload = [{"id": i, "choices": c, "answer": a} for i, c, a in rows]
        (data / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tasks


def write_checkpoint(root: Path) -> Path:
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.serialization import save_model_to_directory
    from xlm.models.transformer import TransformerBaseline

    cfg = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=128,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    ckpt = root / "model_ckpt"
    save_model_to_directory(TransformerBaseline(cfg, seed=42), ckpt)
    return ckpt


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    t = write_tasks(target)
    c = write_checkpoint(target)
    print(json.dumps({"tasks": str(t), "checkpoint": str(c)}, indent=2))
