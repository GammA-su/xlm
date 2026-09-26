"""Mix-01 6B/6.6B/32M quota tables (offline, data-only).

Validates that the quota file matches the active preset weights exactly:
FINAL = weight * 6B, headroom = 110% of FINAL, pilot = weight * 32M, with
exact totals. No network, no real data, no tokenizer training.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import yaml

from xlm.data.sources.mix01 import load_mixture_preset, validate_preset_weights_exact

REPO_ROOT = Path(__file__).resolve().parents[1]
QUOTAS_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01_quotas_6b.yaml"
PRESET_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01.yaml"

FINAL_TOTAL = 6_000_000_000
PILOT_TOTAL = 32_000_000

EXPECTED_WEIGHTS = {
    "essential_science": 0.10,
    "essential_practical": 0.10,
    "essential_prose": 0.05,
    "ultrax_ultrafineweb": 0.20,
    "finepdfs_en": 0.15,
    "synth_en_explanations": 0.15,
    "nemotron_wiki_rewrite": 0.08,
    "finewiki_en": 0.05,
    "ifm_behaviors_general_planning": 0.05,
    "common_pile_prose": 0.05,
    "simple_stories": 0.02,
}


def _load_quotas() -> dict[str, object]:
    data = yaml.safe_load(QUOTAS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_preset_has_eleven_components_summing_exactly_to_one() -> None:
    preset = load_mixture_preset(PRESET_PATH)
    assert validate_preset_weights_exact(preset) == Fraction(1)
    assert dict(preset.weights) == EXPECTED_WEIGHTS
    assert len(preset.weights) == 11
    assert "nemotron_organic_high" not in preset.weights
    assert "nemotron_organic_medium_high" not in preset.weights


def test_final_quotas_are_weight_times_6b() -> None:
    quotas = _load_quotas()
    assert quotas["final_valid_targets"] == FINAL_TOTAL
    final = quotas["final_quotas"]
    assert set(final) == set(EXPECTED_WEIGHTS)
    for component, weight in EXPECTED_WEIGHTS.items():
        assert final[component] == int(weight * FINAL_TOTAL), component
    assert sum(final.values()) == FINAL_TOTAL


def test_headroom_is_110_percent_estimated_usable() -> None:
    quotas = _load_quotas()
    final = quotas["final_quotas"]
    headroom = quotas["first_pass_headroom_quotas"]
    assert set(headroom) == set(EXPECTED_WEIGHTS)
    for component in EXPECTED_WEIGHTS:
        assert headroom[component] == int(final[component] * 1.1), component
    assert sum(headroom.values()) == 6_600_000_000


def test_pilot_quotas_are_weight_times_32m() -> None:
    quotas = _load_quotas()
    assert quotas["pilot_valid_targets"] == PILOT_TOTAL
    pilot = quotas["pilot_quotas"]
    assert set(pilot) == set(EXPECTED_WEIGHTS)
    for component, weight in EXPECTED_WEIGHTS.items():
        assert pilot[component] == int(weight * PILOT_TOTAL), component
    assert sum(pilot.values()) == PILOT_TOTAL
    assert pilot["ultrax_ultrafineweb"] == 6_400_000
