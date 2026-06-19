"""One-time platform bootstrap — ``sow init`` (Phase 9).

Idempotent environment setup: checks for required tools, creates the config and
runtime directory trees, writes the user NGINX ``nginx.conf`` (with the include
line for generated server blocks), creates and enables the platform systemd user
units (NGINX + cloudflared), and prints MCP integration guidance.

This runs once on a fresh host (or any time to repair a missing file — it never
overwrites operator-edited configs). ``install.sh`` delegates to it.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from sow.constants import CLOUDFLARED_UNIT
from sow.paths import RuntimePaths
from sow.sysdeps import nginx, systemctl
from sow.sysdeps.run import Runner, SubprocessError

__all__ = ["InitResult", "init"]

# User-owned config directory (rfc.md §6).
_CONFIG_DIR = Path.home() / ".config" / "sow"
_NGINX_CONF_DIR = _CONFIG_DIR / "nginx"
_NGINX_CONF_PATH = _NGINX_CONF_DIR / "nginx.conf"

# Minimal default sow.yaml — operator edits before the first apply.
_DEFAULT_CONFIG = """version: 1
# Add services below, then run `sow apply`
services: {}
"""

# Path under the user-owned config dir for the NGINX main config (rfc.md §6).
_NGINX_CONF_TEXT = """daemon off;
error_log /tmp/sow-nginx-error.log warn;
pid /tmp/sow-nginx.pid;

events {}

http {
    include ~/.local/share/sow/generated/nginx/servers.conf;

    access_log /tmp/sow-nginx-access.log;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
}
"""

_NGINX_UNIT_TEXT = """[Unit]
Description=sow user-level NGINX
After=network.target

[Service]
ExecStart=nginx -c %h/.config/sow/nginx/nginx.conf
Restart=on-failure
Type=simple

[Install]
WantedBy=default.target
"""

_CLOUDFLARED_UNIT_TEXT = """[Unit]
Description=sow cloudflared tunnel
After=network.target
Wants=network.target

[Service]
ExecStart=cloudflared tunnel run
Restart=on-failure
Type=simple

[Install]
WantedBy=default.target
"""


class InitResult:
    """Outcome of ``init``."""

    ok: bool
    message: str

    def __init__(self, ok: bool, message: str = "") -> None:
        self.ok = ok
        self.message = message


def init(
    runner: Runner,
    paths: RuntimePaths | None = None,
) -> InitResult:
    """Bootstrap the platform. Idempotent — safe to re-run.

    Returns an :class:`InitResult`. On failure the message lists what went wrong.
    """
    paths = paths or RuntimePaths()

    steps: list[str] = []
    ok = True

    # 1. Environment checks.
    checks = _check_env(runner)
    ok = ok and all(status for _name, status, _hint in checks)
    for name, status, remediation in checks:
        _append(steps, f"  {name}: {'ok' if status else 'MISSING'}")
        if not status:
            _append(steps, f"    {remediation}")

    # 2. Create runtime directory tree.
    for d in (paths.repos, paths.data, paths.systemd, paths.nginx, paths.cloudflared):
        d.mkdir(parents=True, exist_ok=True)
    _append(steps, "  runtime directories: ok")

    # 3. Create default config (never overwrite operator-edited file).
    _try_write(_CONFIG_DIR / "sow.yaml", _DEFAULT_CONFIG, steps, "default config")

    # 4. Write NGINX nginx.conf (never overwrite).
    _NGINX_CONF_DIR.mkdir(parents=True, exist_ok=True)
    _try_write(_NGINX_CONF_PATH, _NGINX_CONF_TEXT, steps, "nginx.conf")

    # 5. Write platform systemd units (never overwrite).
    paths.user_units.mkdir(parents=True, exist_ok=True)
    nginx_unit = paths.user_units / f"{nginx.NGINX_UNIT}.service"
    _try_write(nginx_unit, _NGINX_UNIT_TEXT, steps, f"{nginx.NGINX_UNIT}.service unit")

    cf_unit = paths.user_units / f"{CLOUDFLARED_UNIT}.service"
    _try_write(cf_unit, _CLOUDFLARED_UNIT_TEXT, steps, f"{CLOUDFLARED_UNIT}.service unit")

    # 6. Daemon-reload to pick up new units.
    try:
        systemctl.daemon_reload(runner)
        _append(steps, "  daemon-reload: ok")
    except SubprocessError as exc:
        _append(steps, f"  daemon-reload: FAILED ({exc})")
        ok = False

    # 7. Enable and start platform units.
    for unit_name, label in [(nginx.NGINX_UNIT, "nginx"), (CLOUDFLARED_UNIT, "cloudflared")]:
        try:
            systemctl.start(runner, f"{unit_name}.service")
            _append(steps, f"  {label} unit: started")
        except (SubprocessError, OSError) as exc:
            _append(steps, f"  {label} unit: start FAILED ({exc})")
            ok = False

    # 8. MCP integration guidance.
    _append(steps, "")
    _append(
        steps,
        "  MCP server: run `sow mcp-server` (stdio; wire into agent config)",
    )
    _append(steps, "  See docs: https://github.com/VictorBusque/sow")
    _append(steps, "  Next: edit ~/.config/sow/sow.yaml then `sow apply`")

    return InitResult(ok=ok, message="\n".join(steps))


# ===========================================================================
# Internal helpers
# ===========================================================================


def _current_user() -> str:
    """Best-effort current username, for remediation hints only."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "<user>"


def _check_env(runner: Runner) -> list[tuple[str, bool, str]]:
    """Check that required tools are callable and that linger is enabled.

    Returns a list of ``(name, status, remediation)`` triples: ``name`` is the
    label printed in the report, ``status`` is whether the check passed, and
    ``remediation`` is the hint shown when it did not.
    """
    result: list[tuple[str, bool, str]] = []

    # Tool presence: each must be callable. A missing tool is installed, hence
    # the "install <name>" hint.
    tool_tests: list[tuple[str, list[str]]] = [
        ("git", ["git", "--version"]),
        ("systemctl (user)", ["systemctl", "--user", "--version"]),
        ("nginx", ["nginx", "-v"]),
        ("cloudflared", ["cloudflared", "version"]),
    ]
    for name, argv in tool_tests:
        hint = f"install {name} and re-run `sow init`"
        try:
            runner.run(argv, check=False)
            result.append((name, True, hint))
        except (OSError, SubprocessError):
            result.append((name, False, hint))

    # Linger: ``loginctl show-user --property Linger`` with no user prints
    # nothing, so the check must name a user. Query by UID — it avoids relying
    # on $USER/$LOGNAME and matches regardless of how the shell was entered.
    uid = str(os.getuid())
    user = _current_user()
    try:
        r = runner.run(
            ["loginctl", "show-user", uid, "--property", "Linger"],
            check=False,
        )
        linger_ok = "yes" in r.stdout
    except (OSError, SubprocessError):
        linger_ok = False
    result.append(
        (
            "linger",
            linger_ok,
            f"run `loginctl enable-linger {user}`, then re-run `sow init`",
        )
    )
    return result


def _try_write(path: Path, text: str, steps: list[str], label: str) -> None:
    """Write ``text`` to ``path`` if it does not already exist (idempotent)."""
    if path.is_file():
        _append(steps, f"  {label}: exists, skipped")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _append(steps, f"  {label}: created")


def _append(steps: list[str], line: str) -> None:
    steps.append(line)
