"""CLI entrypoint for XLM."""

from __future__ import annotations

import sys

import typer

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

from xlm import __version__
from xlm.cli.artifact_cmd import artifact_app
from xlm.cli.compare_cmd import compare_command, promote_command
from xlm.cli.config_cmd import config_app
from xlm.cli.data_cmd import app as data_app
from xlm.cli.demo_cmd import demo_command
from xlm.cli.doctor import run_doctor
from xlm.cli.eval_cmd import evaluate_command
from xlm.cli.experiment_cmd import campaign_app, experiment_app, queue_app
from xlm.cli.export_cmd import export_command
from xlm.cli.final_cmd import final_app, release_app
from xlm.cli.generate_cmd import generate_command, session_command
from xlm.cli.mixture_cmd import mixture_app
from xlm.cli.model_cmd import model_app
from xlm.cli.prepare_cmd import maintenance_cleanup_command, prepare_command
from xlm.cli.profile_cmd import profile_command
from xlm.cli.report_cmd import dashboard_command, report_command, runs_app
from xlm.cli.research_cmd import research_app
from xlm.cli.tokenizer_cmd import app as tokenizer_app
from xlm.cli.train_cmd import resume_command, run_app, train_command

app = typer.Typer(
    name="xlm",
    help="XLM: Research platform for causal language model pretraining and evaluation.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(config_app, name="config")
app.add_typer(artifact_app, name="artifact")
app.add_typer(data_app, name="data")
app.add_typer(tokenizer_app, name="tokenizer")
app.add_typer(model_app, name="model")
app.add_typer(mixture_app, name="mixture")
app.add_typer(run_app, name="run")
app.add_typer(experiment_app, name="experiment")
app.add_typer(queue_app, name="queue")
app.add_typer(campaign_app, name="campaign")
app.add_typer(research_app, name="research")
app.add_typer(final_app, name="final")
app.add_typer(release_app, name="release")
app.add_typer(runs_app, name="runs")

app.command(name="train")(train_command)
app.command(name="resume")(resume_command)
app.command(name="compare")(compare_command)
app.command(name="promote")(promote_command)
app.command(name="profile")(profile_command)
app.command(name="prepare")(prepare_command)
app.command(name="maintenance")(maintenance_cleanup_command)
app.command(name="report")(report_command)
app.command(name="dashboard")(dashboard_command)
app.command(name="evaluate")(evaluate_command)
app.command(name="generate")(generate_command)
app.command(name="generate-session")(session_command)
app.command(name="export")(export_command)
app.command(name="demo")(demo_command)


def version_callback(value: bool) -> None:
    """Print package version and exit."""
    if value:
        typer.echo(f"xlm {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(  # noqa: ARG001
        False,
        "--version",
        "-v",
        help="Show XLM package version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """XLM root command callback."""
    # Top-level options handling


@app.command(name="doctor")
def doctor_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output doctor diagnostic report strictly as JSON.",
    ),
) -> None:
    """Run diagnostic checks on the environment, hardware, and capabilities."""
    run_doctor(as_json=json_output)


if __name__ == "__main__":
    app()
