"""Tests for the render engine (engine/render.py).

DoD core: the rendered unit carries the exact ExecStart, WorkingDirectory, and
Environment lines for a declared-listen TCP service; a unix-socket service omits
PORT and sets ADDRESS to the socket path; ``${DATA_DIR}`` interpolation resolves;
env_file -> EnvironmentFile lines in order; an unresolved ``${VAR}`` raises
RenderError. Assertions run against both build_spec() (structured) and the
rendered text (template wiring).
"""

from __future__ import annotations

import pytest

from outpost.engine.render import RenderError, build_spec, render_unit
from outpost.models import OutpostConfig, Service
from tests.unit.conftest import minimal_config, minimal_service


def _service(**overrides) -> Service:
    return OutpostConfig.model_validate(
        minimal_config({"api": minimal_service(**overrides)})
    ).services["api"]


# ---------------------------------------------------------------------------
# declared-listen TCP service
# ---------------------------------------------------------------------------


def test_declared_tcp_listen_sets_address_and_port():
    svc = _service(listen="127.0.0.1:8080")
    spec = build_spec("api", svc, port=None)
    env = dict(line.split("=", 1) for line in spec.environment)
    assert env["ADDRESS"] == "127.0.0.1:8080"
    assert env["PORT"] == "8080"


def test_allocated_port_sets_127_0_0_1_address():
    svc = _service()  # no listen -> allocated
    spec = build_spec("api", svc, port=18005)
    env = dict(line.split("=", 1) for line in spec.environment)
    assert env["ADDRESS"] == "127.0.0.1:18005"
    assert env["PORT"] == "18005"


# ---------------------------------------------------------------------------
# unix-socket service
# ---------------------------------------------------------------------------


def test_unix_listen_omits_port_and_address_is_socket_path():
    svc = _service(listen="/run/outpost/api.sock")
    spec = build_spec("api", svc, port=None)
    env = dict(line.split("=", 1) for line in spec.environment)
    assert env["ADDRESS"] == "/run/outpost/api.sock"
    assert "PORT" not in env  # unix sockets have no TCP port


# ---------------------------------------------------------------------------
# ${VAR} interpolation
# ---------------------------------------------------------------------------


def test_data_dir_interpolated_in_env_value():
    svc = _service(environment={"DB": "${DATA_DIR}/app.db"})
    spec = build_spec("api", svc, port=18000)
    env = dict(line.split("=", 1) for line in spec.environment)
    assert env["DB"].endswith("/.local/share/outpost/data/api/app.db")


def test_address_interpolated_in_args():
    svc = _service(listen="127.0.0.1:8080", args=["--addr", "${ADDRESS}"])
    spec = build_spec("api", svc, port=None)
    assert spec.exec_start == "./run --addr 127.0.0.1:8080"


def test_unresolved_var_raises_render_error():
    svc = _service(environment={"X": "${NOPE}"})
    with pytest.raises(RenderError, match=r"unresolved \$\{NOPE\}"):
        build_spec("api", svc, port=18000)


# ---------------------------------------------------------------------------
# working directory + env files
# ---------------------------------------------------------------------------


def test_working_directory_uses_source_path_subdir():
    svc = _service(**{"source": {"git": "https://x.git", "sha": "abc1234", "path": "svc/api"}})
    spec = build_spec("api", svc, port=18000)
    assert spec.working_directory.endswith("/repos/api/svc/api")


def test_env_files_rendered_in_order():
    svc = _service(env_file=["./secrets/a.env", "./secrets/b.env"])
    spec = build_spec("api", svc, port=18000)
    assert spec.environment_files == ["./secrets/a.env", "./secrets/b.env"]


# ---------------------------------------------------------------------------
# platform env precedence over inline
# ---------------------------------------------------------------------------


def test_platform_env_present_alongside_inline():
    # Inline env for non-platform keys coexists with injected PORT/ADDRESS.
    # (Setting inline PORT/ADDRESS while `listen` is set is a model-level error,
    # so precedence is exercised only for operator-defined keys.)
    svc = _service(environment={"LOG_LEVEL": "info"})
    spec = build_spec("api", svc, port=18000)
    env = dict(line.split("=", 1) for line in spec.environment)
    assert env["LOG_LEVEL"] == "info"
    assert env["PORT"] == "18000"
    assert env["ADDRESS"] == "127.0.0.1:18000"


# ---------------------------------------------------------------------------
# rendered text (template wiring)
# ---------------------------------------------------------------------------


def test_rendered_unit_contains_key_directives():
    svc = _service(
        listen="127.0.0.1:8080",
        command="./bin/web",
        args=["serve"],
        env_file=["./secrets/web.env"],
        restart="always",
    )
    text = render_unit("web", svc, port=None)
    assert "[Unit]" in text and "[Service]" in text and "[Install]" in text
    assert "WorkingDirectory=" in text
    assert text.count("EnvironmentFile=./secrets/web.env") == 1
    assert "Environment=ADDRESS=127.0.0.1:8080" in text
    assert "ExecStart=./bin/web serve" in text
    assert "Restart=always" in text
    assert "RestartSec=" in text
    assert "WantedBy=default.target" in text


def test_rendered_unit_omits_environmentfile_when_none():
    svc = _service(listen="127.0.0.1:8080")
    text = render_unit("web", svc, port=None)
    assert "EnvironmentFile=" not in text
