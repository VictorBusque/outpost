"""Smoke tests for the CLI commands (validate + render) via CliRunner.

DoD core: ``validate`` exits 0 on a valid config and 2 on an invalid one;
``render <service>`` prints a unit and exits 2 for a missing service or invalid
config.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from outpost.cli.app import app

runner = CliRunner()

VALID_YAML = (
    "version: 1\n"
    "services:\n"
    "  api:\n"
    "    source: {git: https://x.git, sha: abc1234}\n"
    "    command: ./run\n"
    "    listen: 127.0.0.1:8080\n"
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "outpost.yaml"
    p.write_text(text)
    return p


def test_validate_valid_exits_zero(tmp_path: Path):
    cfg = _write(tmp_path, VALID_YAML)
    result = runner.invoke(app, ["validate", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "valid" in result.stdout


def test_validate_invalid_exits_two(tmp_path: Path):
    cfg = _write(tmp_path, "version: 1\nservices:\n  api:\n    command: ./run\n")  # missing source
    result = runner.invoke(app, ["validate", "--config", str(cfg)])
    assert result.exit_code == 2


def test_render_prints_unit(tmp_path: Path):
    cfg = _write(tmp_path, VALID_YAML)
    result = runner.invoke(app, ["render", "api", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "[Unit]" in result.stdout
    assert "ExecStart=./run" in result.stdout
    assert "Environment=PORT=8080" in result.stdout


def test_render_unknown_service_exits_two(tmp_path: Path):
    cfg = _write(tmp_path, VALID_YAML)
    result = runner.invoke(app, ["render", "nope", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "unknown service" in (result.stdout + result.output)


def test_render_allocated_port_service(tmp_path: Path):
    # No `listen` -> port allocated from the default range; rendered ADDRESS reflects it.
    cfg = _write(
        tmp_path,
        "version: 1\nservices:\n  api:\n    source: {git: https://x.git, sha: abc1234}\n    command: ./run\n",
    )
    result = runner.invoke(app, ["render", "api", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "Environment=ADDRESS=127.0.0.1:18000" in result.stdout
    assert "Environment=PORT=18000" in result.stdout
