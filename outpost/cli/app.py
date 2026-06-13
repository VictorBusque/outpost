"""Outpost CLI — the operator interface.

Phase 0 scaffolding only: a minimal Typer app wired to the console-script entry
point (`outpost = "outpost.cli.app:app"`). Commands are added in Phase 7.
"""

from __future__ import annotations

import typer

from outpost import __version__

app = typer.Typer(
    name="outpost",
    help="Turn a YAML file into running systemd user services behind NGINX.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"outpost {__version__}")
        raise typer.Exit


@app.callback()
def _main(
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """Outpost — a Linux micro-platform control plane."""
