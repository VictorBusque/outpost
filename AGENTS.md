# AGENTS.md

Guidance for coding agents and contributors working in this repository.

## What Outpost is

Outpost is a **deterministic, single-node Linux micro-platform control plane.** It takes one declarative YAML file and turns it into running `systemd --user` services behind a user-level NGINX reverse proxy, exposed publicly through Cloudflare Tunnel. It also ships a stdio MCP server so coding agents can operate the platform through typed tools instead of raw shell access.

It is a **deployment engine, not an orchestrator**: one machine, one config, one execution loop. No containers, no schedulers, no root daemons.

## Current state of this repo

**Specification-only.** There is no implementation yet — only this `AGENTS.md`, `README.md`, `LICENSE`, and the design docs under `docs/v1/`. Treat the docs as the source of truth for intended behavior and conventions; any code you write must conform to them.

## The single loop

> A YAML file defines git-sourced services and host/path routes → Outpost renders systemd user units and NGINX config → services run behind a Cloudflare Tunnel.

Everything in v1 is derived from this loop. The four primitives are **service**, **route**, **apply**, and **update**.

## Where the design lives

Read these before writing or reviewing code:

- `docs/v1/prd.md` — product requirements: primitives, apply pipeline, listener/port model, health, source/update semantics, CLI + MCP surface.
- `docs/v1/rfc.md` — technical spec: install/init model, directory layout, execution model, state model, security posture. **The canonical v1 CLI command surface is in `rfc.md` §19.**
- `docs/v1/stack.md` — language, libraries, codebase layout, and engineering conventions.

When the docs disagree, `rfc.md` is the authoritative reference for the exact command surface and file layout; raise the discrepancy rather than silently picking one.

## Target tech stack

- **Language:** Python 3.12+
- **Tooling:** `uv` (env + deps), `ruff` (format + lint), `ty` (strict type checking), `pytest`
- **Libraries:** Typer (CLI), Pydantic v2 (schema), Jinja2 (config templates), `mcp` SDK (agent interface), native `subprocess` (system calls — no heavy wrappers)

## Target codebase layout

```
outpost/
├── cli/         # Typer command definitions (human interface)
├── mcp/         # stdio server + tool definitions (agent interface)
├── engine/      # core loop: validate, render, apply, update
├── models/      # Pydantic schemas (Service, Route, ConfigState)
├── templates/   # Jinja2 templates (.service, .conf, cloudflared yaml)
└── sysdeps/     # subprocess wrappers (git, systemctl, nginx)
tests/
├── unit/        # logic + schema validation
├── integration/ # template rendering + state machine
└── mocks/       # mocked sysdeps (no host side-effects)
```

## Engineering rules (non-negotiable)

These come straight from the docs and apply to all system-mutating code.

**Fail-fast.** Catch `subprocess.CalledProcessError`, log the exact stderr, and exit the run loop with a non-zero code. Never attempt partial recovery mid-apply.

**Atomicity.** Render configs to a temp dir, validate (`nginx -t`), then swap via `os.replace()`. State files (`state.json`) and config rewrites (`outpost.yaml`) must be written to a `.tmp` file and atomically renamed. v1 applies are all-or-nothing: on invalid config or failed startup health check, revert to the last-known-good backup.

**Idempotency.** `apply` compares the spec digest to the stored applied digest and no-ops if they match and services are up. `systemctl`/`git` wrappers must no-op when the desired state is already met.

**Strict typing.** `ty` in strict mode across CLI and MCP schemas. No dynamic type drift in engine logic.

**Pure, immutable models.** Once `outpost.yaml` is parsed into a Pydantic model, treat it as immutable; mutations return a new instance to be saved.

## Source-of-truth facts to keep straight

- **Deployed SHA lives in the config** (`services.<name>.source.sha`), not in `state.json`. `state.json` holds only the applied spec digest, allocated ports, and apply timestamps — nothing else (no SQLite in v1).
- **`apply` never advances commits; `update` is the only command that writes `source.sha`.** `apply` reconciles to the pinned sha; `update` fetches, resolves `ref`→sha, writes it, then applies.
- **Rootless everywhere.** Services run as `systemd --user` units; no root, no sudoers rule in v1. NGINX is also a user unit listening on `127.0.0.1:41999`; cloudflared points at it.
- **Ports:** declared via `listen:` or allocated from a loopback range; injected into the service as `PORT`, `ADDRESS`, and `DATA_DIR`. Platform-injected vars override operator env; declaring `listen` and also setting `PORT`/`ADDRESS` is a validation error.
- **Health is a startup gate only** — it makes/breaks an apply. No passive/active probes, no live upstream pruning in v1.
- **Secrets:** Compose-style `environment` / `env_file`. No built-in secret store. `status` and `logs` never echo environment contents.

## Paths (XDG-strict)

- Config (user-owned, editable): `~/.config/outpost/outpost.yaml`
- Runtime (Outpost-owned, do not hand-edit): `~/.local/share/outpost/` — `repos/`, `data/`, `generated/{nginx,cloudflared,systemd}/`, `state.json`

## Local development (once code exists)

```bash
uv sync                 # install deps
uvx ruff check .        # lint
uvx ruff format .       # format
uvx ty .                # type check (strict)
uv run pytest           # tests — mock subprocess.run + filesystem in engine tests
```

## Conventions for editing this repo right now

- Markdown only so far. Keep docs and README internally consistent; the README's CLI table must match `rfc.md` §19.
- Don't introduce code until the layout above is scaffolded; when you do, follow `stack.md` exactly.
- If a doc says X and another says Y, flag it rather than guessing.
