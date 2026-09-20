"""P23: enumerate the registered command tree and exercise every help surface."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from typer._click.core import Command
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from xlm.cli.main import app


def command_paths(command: Command, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    yield prefix
    if isinstance(command, TyperGroup):
        for name, child in sorted(command.commands.items()):
            yield from command_paths(child, (*prefix, name))


@pytest.mark.parametrize(
    "path", list(command_paths(get_command(app))), ids=lambda p: "xlm " + " ".join(p)
)
def test_every_registered_command_help(path: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app, [*path, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
