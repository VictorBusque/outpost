"""Render an immutable config model into a systemd user unit.

The pipeline is: model → a structured :class:`UnitSpec` value → Jinja2 template.
Keeping the Python side responsible for *all* policy (env precedence, ``${VAR}``
interpolation, path resolution) and the template responsible only for
presentation means the template stays thin and the logic is unit-testable
without rendering.

Env precedence (config-schema.md §"Platform-injected environment"):
platform-injected (``PORT``/``ADDRESS``/``DATA_DIR``) highest, then inline
``environment``. ``${VAR}`` interpolation is resolved at render time against the
*platform-injected* set — the only vars that exist deterministically at generate
time. An unknown ``${VAR}`` fails fast rather than emitting a literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template

from outpost.models import Service

__all__ = ["RenderError", "UnitSpec", "render_unit"]

# ${VAR} — letters/digits/underscore, braced only (we don't interpolate bare $FOO).
_VAR_RE: re.Pattern[str] = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")

# Sane crash-loop throttle (config-schema.md: "throttled, not hammering").
_RESTART_SEC: str = "5"
_START_LIMIT_BURST: str = "5"
_START_LIMIT_INTERVAL_SEC: str = "30"

# Runtime layout (XDG-strict, AGENTS.md "Paths").
_REPO_ROOT = Path.home() / ".local" / "share" / "outpost" / "repos"
_DATA_ROOT = Path.home() / ".local" / "share" / "outpost" / "data"

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


class RenderError(Exception):
    """Raised when a service cannot be rendered (e.g. an unknown ``${VAR}``)."""


@dataclass(frozen=True)
class UnitSpec:
    """The fully-resolved data a unit template renders from.

    Every field is plain text (the template does no policy). Environment entries
    are pre-formatted ``KEY=value`` lines in precedence order; env files are the
    operator-declared paths verbatim.
    """

    description: str
    working_directory: str
    environment: list[str] = field(default_factory=list)
    environment_files: list[str] = field(default_factory=list)
    exec_start: str = ""
    restart: str = "on-failure"
    restart_sec: str = _RESTART_SEC
    start_limit_burst: str = _START_LIMIT_BURST
    start_limit_interval_sec: str = _START_LIMIT_INTERVAL_SEC


def _template() -> Template:
    """Load the service unit template (shipped with the package)."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("service.j2")


def render_unit(name: str, service: Service, port: int | None) -> str:
    """Render ``service``'s systemd unit.

    ``port`` is the service's TCP port — declared (from ``listen``) or allocated.
    ``None`` marks a unix-socket service (then ``PORT`` is omitted and
    ``ADDRESS`` is the socket path).
    """
    spec = build_spec(name, service, port)
    return _template().render(spec=spec)


def build_spec(name: str, service: Service, port: int | None) -> UnitSpec:
    """Compute the resolved :class:`UnitSpec` for ``service``.

    Separated from :func:`render_unit` so tests assert on structured data without
    going through the template.
    """
    is_unix = service.is_unix_listen
    if is_unix:
        address = service.listen
    elif service.has_listen:
        assert service.parsed_listen_port() is not None
        address = service.listen
    elif port is not None:
        address = f"127.0.0.1:{port}"
    else:
        raise RenderError(f"service {name!r} has no listen and no allocated port; cannot render")

    data_dir = _DATA_ROOT / name
    platform_env = {
        "ADDRESS": address,
        "DATA_DIR": str(data_dir),
    }
    if not is_unix and port is not None:
        platform_env["PORT"] = str(port)
    elif not is_unix and service.has_listen:
        # Declared listen carries its own port; surface it for the service to bind.
        listen_port = service.parsed_listen_port()
        assert listen_port is not None
        platform_env["PORT"] = str(listen_port)

    # Precedence: platform-injected wins over inline. Interpolation resolves
    # against the merged set so `${DATA_DIR}` in inline env works.
    merged: dict[str, str] = {**dict(service.environment.items()), **platform_env}
    resolved = {k: _interpolate(name, v, merged) for k, v in merged.items()}
    environment_lines = [f"{k}={v}" for k, v in resolved.items()]

    # ExecStart = command + interpolated args.
    exec_args = [service.command, *(_interpolate(name, a, merged) for a in service.args)]
    exec_start = " ".join(exec_args)

    clone_dir = _REPO_ROOT / name
    working_dir = clone_dir / service.source.path if service.source.path else clone_dir

    return UnitSpec(
        description=f"Outpost service: {name}",
        working_directory=str(working_dir),
        environment=environment_lines,
        environment_files=list(service.env_file),
        exec_start=exec_start,
        restart=service.restart,
    )


def _interpolate(name: str, value: str, env: dict[str, str]) -> str:
    """Resolve ``${VAR}`` references against ``env``; unknown vars fail fast."""

    def replace(match: re.Match[str]) -> str:
        var = match.group("name")
        if var not in env:
            raise RenderError(
                f"service {name!r}: unresolved ${{{var}}} in value {value!r} "
                f"(available: {sorted(env)})"
            )
        return env[var]

    return _VAR_RE.sub(replace, value)
