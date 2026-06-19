"""MCP stdio server — 11 tools matching ``cli-reference.md``.

Each tool is a thin async adapter over the engine functions or sysdeps wrappers
the CLI uses. The server is single-connection stdio: launched as a subprocess by
the MCP client (a coding-agnent harness or IDE), it reads JSON-RPC requests from
stdin and writes responses to stdout.

Engine and sysdeps calls are synchronous; the event loop runs them in a thread
via ``asyncio.to_thread`` so the server stays responsive to the client during
subprocess-heavy operations (apply, update, logs).
"""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

from mcp.types import CallToolResult, TextContent, Tool

from outpost import config as config_mod
from outpost.engine.apply import apply as _apply
from outpost.engine.update import update as _update
from outpost.paths import RuntimePaths
from outpost.state.store import State, StateStore
from outpost.sysdeps import systemctl
from outpost.sysdeps.journalctl import tail as _journalctl_tail
from outpost.sysdeps.run import RealRunner, SubprocessError

# Production runner (mocked in tests).
_runner = RealRunner()

# Injectable config path (set by tests to avoid touching ~/.config/outpost/).
_config_path: str | Path | None = None


async def run_server() -> None:
    """Run the stdio MCP server. Called by the CLI ``mcp-server`` command."""
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("outpost")

    # -----------------------------------------------------------------------
    # Tool definitions
    # -----------------------------------------------------------------------

    _TOOLS: list[Tool] = [
        Tool(
            name="list_services",
            description="List all configured services with their systemd unit state.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_service_status",
            description=(
                "Get detailed status for one service: unit state, listen, sha, ref, health."
            ),
            inputSchema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ),
        Tool(
            name="start_service",
            description="Start a service (systemctl --user start).",
            inputSchema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ),
        Tool(
            name="stop_service",
            description="Stop a service (systemctl --user stop).",
            inputSchema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ),
        Tool(
            name="restart_service",
            description="Restart a service (systemctl --user restart).",
            inputSchema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ),
        Tool(
            name="update_service",
            description="Fetch latest commit for a service's ref, write the new SHA, and apply.",
            inputSchema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "ref": {"type": "string", "description": "Override tracked ref (optional)"},
                },
                "required": ["service"],
            },
        ),
        Tool(
            name="apply_config",
            description=(
                "Materialize sources, generate configs, swap, health-gate, commit."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="validate_config",
            description="Parse and validate the config with no system mutation.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="show_routes",
            description="List configured host/path routes.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="show_exposure",
            description="List hosts exposed through Cloudflare Tunnel.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="tail_logs",
            description="Bounded tail of journald logs for a service (default 200 lines).",
            inputSchema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "lines": {"type": "integer", "default": 200},
                },
                "required": ["service"],
            },
        ),
    ]

    # -----------------------------------------------------------------------
    # Handlers
    # -----------------------------------------------------------------------

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict | None
    ) -> CallToolResult:
        args = arguments or {}
        try:
            match name:
                case "list_services":
                    return await _list_services()
                case "get_service_status":
                    return await _get_service_status(str(args["service"]))
                case "start_service":
                    return await _run_syscmd("start", str(args["service"]))
                case "stop_service":
                    return await _run_syscmd("stop", str(args["service"]))
                case "restart_service":
                    return await _run_syscmd("restart", str(args["service"]))
                case "update_service":
                    return await _update_service(
                        str(args["service"]),
                        str(args["ref"]) if "ref" in args else None,
                    )
                case "apply_config":
                    return await _apply_config()
                case "validate_config":
                    return await _validate_config()
                case "show_routes":
                    return await _show_routes()
                case "show_exposure":
                    return await _show_exposure()
                case "tail_logs":
                    return await _tail_logs(str(args["service"]), int(args.get("lines", 200)))
                case _:
                    return _error(f"unknown tool: {name}")
        except Exception as exc:
            traceback.print_exc()
            return _error(str(exc))

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# ===========================================================================
# Tool implementations
# ===========================================================================


async def _list_services() -> CallToolResult:
    config = _load()
    services = []
    for name in config.services:
        state = await _unit_state(name)
        svc = config.services[name]
        services.append({
            "name": name,
            "unit": state,
            "listen": svc.listen or None,
            "sha": svc.source.sha or None,
            "ref": svc.source.ref,
        })
    return _ok(services)


async def _get_service_status(name: str) -> CallToolResult:
    config = _load()
    svc = config.services.get(name)
    if svc is None:
        return _error(f"service {name!r} not found")
    state = await _unit_state(name)
    return _ok({
        "name": name,
        "unit": state,
        "listen": svc.listen or None,
        "sha": svc.source.sha or None,
        "ref": svc.source.ref,
        "health": {"defined": svc.health is not None},
    })


async def _run_syscmd(action: str, name: str) -> CallToolResult:
    fn = {
        "start": lambda: systemctl.start(_runner, f"{name}.service"),
        "stop": lambda: systemctl.stop(_runner, f"{name}.service"),
        "restart": lambda: systemctl.restart(_runner, f"{name}.service"),
    }.get(action)
    if fn is None:
        return _error(f"unknown action: {action}")
    try:
        await asyncio.to_thread(fn)
        state = await _unit_state(name)
        return _ok({"service": name, "unit": state})
    except (SubprocessError, OSError) as exc:
        return _error(f"{action} failed: {exc}")


async def _update_service(name: str, ref: str | None = None) -> CallToolResult:
    config = _load()
    svc = config.services.get(name)
    if svc is None:
        return _error(f"service {name!r} not found")
    old_sha = svc.source.sha or ""
    result = await asyncio.to_thread(
        lambda: _update(
            name,
            runner=_runner,
            ref=ref,
            store=StateStore(RuntimePaths().state),
        )
    )
    if result.ok:
        return _ok({
            "service": name,
            "old_sha": old_sha,
            "new_sha": _load().services[name].source.sha,
            "applied": True,
        })
    return _ok({"service": name, "applied": False, "error": result.message})


async def _apply_config() -> CallToolResult:
    result = await asyncio.to_thread(
        lambda: _apply(runner=_runner, store=StateStore(RuntimePaths().state))
    )
    if result.ok:
        return _ok({
            "applied": True,
            "digest": _load_state().applied_digest,
            "reverted": False,
            "services": list(_load().services),
        })
    return _ok({"applied": False, "reverted": not result.ok, "error": result.message})


async def _validate_config() -> CallToolResult:
    try:
        _load()
        return _ok({"valid": True})
    except config_mod.ConfigError as exc:
        errors = [{"path": p, "message": m} for p, m in _parse_errors(exc)]
        return _ok({"valid": False, "errors": errors})


async def _show_routes() -> CallToolResult:
    config = _load()
    routes = [
        {
            "host": r.host or None,
            "paths": [{"prefix": p, "to": t.to} for p, t in r.paths.items()],
        }
        for r in config.routes
    ]
    return _ok(routes)


async def _show_exposure() -> CallToolResult:
    config = _load()
    if config.exposure is None:
        return _ok({"provider": None, "hosts": []})
    return _ok({
        "provider": "cloudflare",
        "hosts": list(config.exposure.cloudflare.hosts),
    })


async def _tail_logs(name: str, lines: int = 200) -> CallToolResult:
    try:
        text = await asyncio.to_thread(
            lambda: _journalctl_tail(_runner, f"{name}.service", lines=lines)
        )
    except (SubprocessError, OSError) as exc:
        return _error(f"error reading logs: {exc}")
    # Split into lines for structured output.
    return _ok({"service": name, "lines": text.rstrip("\n").split("\n")})


# ===========================================================================
# Internal helpers
# ===========================================================================


def _load() -> config_mod.OutpostConfig:
    """Load the config; raise ConfigError on failure (bubbles to _error)."""
    return config_mod.load(_config_path)


def _load_state() -> State:
    """Load the current state sidecar (returns empty state on missing file)."""
    return StateStore(RuntimePaths().state).load()


async def _unit_state(name: str) -> str:
    try:
        return await asyncio.to_thread(
            systemctl.unit_state, _runner, f"{name}.service"
        )
    except (SubprocessError, OSError):
        return "unknown"


def _parse_errors(exc: config_mod.ConfigError) -> list[tuple[str, str]]:
    """Extract (path, message) pairs from a ConfigError."""
    if not exc.errors:
        return [("<root>", str(exc))]
    return [(e.split(":", 1)[0].strip(), e.split(":", 1)[-1].strip()) for e in exc.errors]


def _ok(data: object) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=_j(data))],
    )


def _error(msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=_j({"error": msg}))],
        isError=True,
    )


def _j(data: object) -> str:
    return json.dumps(data, indent=2, default=str)
