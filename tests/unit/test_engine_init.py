"""Tests for ``sow init`` environment checks (``sow/engine/init.py``).

Focus: the linger check. ``loginctl show-user --property Linger`` with *no* user
returns empty output, so a naive check always reads "not lingering". The check
must name a user (here, by UID) — these tests lock that contract in.
"""

from __future__ import annotations

import os

from sow.engine.init import _check_env
from sow.sysdeps.run import CompletedProcess
from tests.mocks.runner import FakeRunner, RecordedCall


def _script_tools(fake: FakeRunner) -> None:
    """Script the four tool-presence probes as successful."""
    for argv in (
        ["git", "--version"],
        ["systemctl", "--user", "--version"],
        ["nginx", "-v"],
        ["cloudflared", "version"],
    ):
        fake.script(argv, returns=CompletedProcess(0, "ok\n", ""))


def _checks_by_name(fake: FakeRunner) -> dict[str, tuple[bool, str]]:
    return {name: (status, hint) for name, status, hint in _check_env(fake)}


def test_linger_detected_when_enabled_for_current_uid():
    """Linger=yes for the current UID → status True, with a run-able hint."""
    fake = FakeRunner()
    _script_tools(fake)
    fake.script(
        ["loginctl", "show-user", str(os.getuid()), "--property", "Linger"],
        returns=CompletedProcess(0, "Linger=yes\n", ""),
    )

    by_name = _checks_by_name(fake)
    status, hint = by_name["linger"]
    assert status is True
    assert "loginctl enable-linger" in hint


def test_linger_missing_when_disabled():
    """Linger=no → status False, and the hint names the command (not 'install')."""
    fake = FakeRunner()
    _script_tools(fake)
    fake.script(
        ["loginctl", "show-user", str(os.getuid()), "--property", "Linger"],
        returns=CompletedProcess(0, "Linger=no\n", ""),
    )

    by_name = _checks_by_name(fake)
    status, hint = by_name["linger"]
    assert status is False
    assert "loginctl enable-linger" in hint
    assert "install" not in hint


def test_linger_query_names_a_user():
    """The lingering argv must include a user/UID argument after ``show-user``.

    Regression guard: ``loginctl show-user --property Linger`` (no user) prints
    nothing, so the check used to always read "not lingering". A user arg must
    be present.
    """
    fake = FakeRunner()
    _script_tools(fake)
    fake.script(
        ["loginctl", "show-user", str(os.getuid()), "--property", "Linger"],
        returns=CompletedProcess(0, "Linger=yes\n", ""),
    )
    _check_env(fake)

    linger_calls = [c.argv for c in fake.calls if c.argv[:2] == ["loginctl", "show-user"]]
    assert linger_calls, "expected a loginctl show-user probe"
    argv = linger_calls[0]
    # argv == ["loginctl", "show-user", <user>, "--property", "Linger"]
    assert len(argv) == 5
    assert argv[2] == str(os.getuid())


def test_missing_tool_reports_install_hint():
    """A genuinely missing tool (OSError, not a bad exit) keeps the install hint."""
    fake = FakeRunner()
    for argv in (
        ["systemctl", "--user", "--version"],
        ["nginx", "-v"],
        ["cloudflared", "version"],
    ):
        fake.script(argv, returns=CompletedProcess(0, "ok\n", ""))

    def _git_not_found(_call: RecordedCall) -> CompletedProcess:
        raise FileNotFoundError(2, "No such file or directory", "git")

    fake.script(["git", "--version"], returns_fn=_git_not_found)
    fake.script(
        ["loginctl", "show-user", str(os.getuid()), "--property", "Linger"],
        returns=CompletedProcess(0, "Linger=yes\n", ""),
    )

    by_name = _checks_by_name(fake)
    status, hint = by_name["git"]
    assert status is False
    assert "install git" in hint
