"""Exercise C05 inventory against a real authored campaign/seal, offline loopback."""

from __future__ import annotations

from typing import Any

from test_essential_web_fast import World
from test_essential_web_pool_seal import run_first_pass, seal_main
from test_essential_web_pool_seal import sealer as sealer
from test_essential_web_pool_seal import served as served
from test_essential_web_pool_seal import world as world
from xlm.data.exclusion.inputs import essential_inputs


def test_essential_seal_inventory(world: World, sealer: Any) -> None:
    run_first_pass(world)
    assert seal_main(world, sealer, "build") == 0
    source, files = essential_inputs(world.root)
    assert len(files) == 12  # Four authored units, three disjoint views each.
    assert {f["component"] for f in files} == set(source["membership"])
    assert sum(f["documents"] for f in files) == sum(
        m["documents"] for m in source["membership"].values()
    )
