# Outpost v1 — Implementation Plan

## Purpose

A roadmap from "spec-only repo" to a working v1: what to build, in what order, and the technical decisions an implementer needs made up front. It is **derived from** `prd.md`, `rfc.md`, `stack.md`, `config-schema.md`, and `cli-reference.md`. It is a living plan, not a normative spec — if it conflicts with those docs, the specs win and the conflict should be flagged.

The unit of work is the **single loop**: a YAML file → rendered systemd user units + NGINX config → services running behind a user NGINX, exposed via Cloudflare Tunnel. Everything below exists to ship that loop safely.

## Guiding principles (non-negotiable)

These come straight from `AGENTS.md` and govern all system-mutating code:

- **Fail-fast.** Catch `subprocess.CalledProcessError`, log exact stderr, exit non-zero. No partial recovery mid-apply.
- **Atomicity.** Render to a temp dir, validate (`nginx -t`), swap via `os.replace()`. `state.json` and `outpost.yaml` writes go through `.tmp` + atomic rename. Applies are all-or-nothing; on invalid config or failed health gate, revert to the single last-known-good backup. NGINX reloads only **after** the health check passes.
- **Idempotency.** `apply` no-ops when the spec digest matches the stored applied digest and services are up. `systemctl`/`git` wrappers no-op when the desired state is already met.
- **Strict typing.** `ty` strict across CLI + MCP schemas (`mypy --strict` fallback). No dynamic type drift in engine logic.
- **Pure, immutable models.** Parsed config is a frozen Pydantic model; mutations return a new instance to be saved.
- **Rootless everywhere.** `systemctl --user` only. No root, no sudoers rule in v1.

## Architecture and dependency direction

```
cli/ (Typer) ─┐
              ├─► engine/ ─► models/   (Pydantic, pure)
mcp/ (stdio) ─┘          ├─► sysdeps/  (subprocess wrappers: git, systemctl, nginx, journalctl)
                        ├─► templates/ (Jinja2)
                        └─► state/     (state.json + port allocator)
```

The rule: **`engine/` knows nothing about Typer or MCP.** It exposes pure functions over `models/` and `sysdeps/`. The CLI and MCP server are thin adapters that call the engine and map results to exit codes / JSON. This makes the engine fully unit-testable with mocked subprocess and lets MCP reuse it verbatim.

Module responsibilities (matching `stack.md` §4):

- `models/` — Pydantic v2 schemas + all validation rules. Frozen, pure.
- `sysdeps/` — subprocess wrappers. Idempotent, fail-fast. The only place `subprocess.run` is called.
- `engine/` — the loop: `validate`, `render`, `apply`, `update`, `health`, `stage`.
- `templates/` — Jinja2 `.j2` files for the systemd unit, NGINX server blocks, cloudflared config.
- `cli/` — Typer commands. Thin.
- `mcp/` — stdio server + the 11 tools. Thin.

## Cross-cutting technical decisions (decide once, up front)

These are the details the specs left to implementation. Pinning them now prevents drift.

**Exit codes.** `0` success (incl. idempotent no-op); `1` operational failure (subprocess fail-fast, apply rollback, service/cloudflared start failure); `2` invalid config (schema/topology, no mutation). See `cli-reference.md`.

**Spec digest.** SHA-256 over canonical JSON (sorted keys) of the **full config including `source.sha`**. Including `sha` is required: `update` changes `sha`, so the digest must change for the subsequent `apply` to be a correct no-op. Recompute the digest **after** the one-time seed writes `sha`. Stored as `applied_digest` in `state.json`.

**Atomic write helper.** `state.io.write_atomic(path, bytes)`: write to `path.tmp`, `os.replace(path.tmp, path)`. Used for `state.json` and every `outpost.yaml` rewrite (seed, `update`). Single utility, tested once, reused everywhere.

**Build-skip marker.** Persist `~/.local/share/outpost/repos/<service>/.outpost/built.sha`. Rebuild iff the marker is missing or `!=` the pinned `sha`; write it after a successful build. This is how `apply` knows an unchanged pinned commit is already built (idempotency) without storing build state in `state.json` (which holds only digest/ports/timestamps).

**Port allocator.** First-fit over `port_range` (default `18000-18999`), excluding `{41999}` ∪ declared `listen` ports ∪ already-allocated ports. Allocations persist in `state.json` keyed by service; reuse an existing allocation unless the service definition changed, else release + reallocate. Exhaustion → raise (fail-fast). Pure function over an in-memory allocation map, so it's trivially unit-testable.

**Health probe (stdlib only, no new dep).** `http` check via `urllib.request` against `http://127.0.0.1:<port><path>` (2xx/3xx passes); `tcp` check via `socket.create_connection`. For a unix-socket `listen`, connect to the socket path. Poll every ~1s up to `health.timeout` (default 30s). Always probe the **local listener**, never NGINX.

**NGINX validation in staging.** `nginx -t` needs a complete `nginx.conf`, not bare server blocks (`stack.md`). In the staging dir, write a throwaway `nginx.conf` whose `http{}` block `include`s the staged `servers/*.conf`, mirroring the live user-unit's include line, then run `nginx -t -c <staging>/nginx.conf` (with the right `-p` prefix). Only `os.replace()` into `generated/nginx/` after it passes.

**Last-known-good backup (single slot).** Before the atomic swap, copy `generated/` → `generated/.lkg/`. Revert = copy `.lkg/` back over `generated/` + `daemon-reload` + `nginx -t` + `nginx reload`. One slot only — there is no history to roll back *through* (non-goal).

**Systemd unit discovery.** Generated units live under `~/.local/share/outpost/generated/systemd/`; to make `systemctl --user` find them, symlink each into `~/.config/systemd/user/` (the canonical user-unit dir) and `daemon-reload`. Confirm during Phase 9 init work.

**NGINX/Cloudflared user units** are platform-managed: `init` writes the user NGINX's `nginx.conf` (with the include line) and enables/starts both units; `apply` ensures cloudflared is started. Supervision is systemd's, not Outpost's.

**Secrets hygiene.** `env_file` → generated unit `EnvironmentFile=` (reference, don't copy). `status`/`logs`/MCP outputs never include env values. Enforce with tests.

## Discrepancies to reconcile before/while implementing

- **NGINX config location.** `prd.md` §1 says server blocks go in `~/.config/outpost/nginx/`; `rfc.md` §6 lists `generated/nginx/` under `~/.local/share/outpost/`. Per `AGENTS.md`, **RFC §6 governs file layout.** Reconciled meaning: the user NGINX's main `nginx.conf` (with the `include` line) is created by `init` under `~/.config/outpost/nginx/`; generated server blocks are runtime under `~/.local/share/outpost/generated/nginx/`. Recommend aligning `prd.md` §1's example to match — flag, don't silently edit.

## Task decomposition

Effort labels are rough (S/M/L). Each phase lists tasks, its dependency, and a definition of done (DoD).

### Phase 0 — Scaffolding (S)
- `pyproject.toml`: `uv`, `requires-python >=3.12`, runtime deps (`typer`, `pydantic>=2`, `jinja2`, `mcp`), dev deps (`ruff`, `ty`, `pytest`).
- Package skeleton: `outpost/{cli,mcp,engine,models,sysdeps,templates,state}/__init__.py`; `tests/{unit,integration,mocks}/__init__.py`.
- `ruff` config (line length, rule set), `ty` strict config, `pytest` config (`testpaths`).
- Console-script entry point `outpost` → `cli.app`.
- *DoD:* `uv sync` works; `uvx ruff check .`, `uvx ty .`, `uv run pytest` all run cleanly (0 tests).

### Phase 1 — Models & config layer (M)
- Pydantic v2 models, all `frozen=True`: `Source`, `Health`, `Service`, `PathTarget`, `Route`, `CloudflareExposure`, `Exposure`, `OutpostConfig`.
- Validators implementing **every** rule in `config-schema.md` §"Validation rules" (listen/PORT/ADDRESS exclusion, port collisions incl. `41999`, duplicate host, `to` references a service, `port_range` parse, `health` exactly-one-of, `restart` enum, `exposure.hosts` ⊆ routed hosts).
- `config.load(path)` → `OutpostConfig` (respect `--config`; default `~/.config/outpost/outpost.yaml`).
- `config.digest(config)` → canonical-SHA256 (incl. `source.sha`).
- *DoD:* unit test per validation rule; digest stability test (same config → same digest; sha change → digest change); `ty` strict clean.

### Phase 2 — sysdeps layer (M)
- `sysdeps/run.py`: typed subprocess runner (`check=True`, capture stderr, raise a typed `SubprocessError` carrying stderr).
- `sysdeps/git.py`: `clone`, `fetch`, `checkout(sha)`, `resolve_ref(ref)→sha`, `current_sha()`.
- `sysdeps/systemctl.py` (user scope): `start/stop/restart`, `is_active`, `unit_state`, `daemon_reload`.
- `sysdeps/nginx.py`: `test(conf_path)`, `reload()`.
- `sysdeps/journalctl.py`: `tail(unit, lines)`.
- All idempotent (no-op when desired state met) and fail-fast.
- *DoD:* mocked-`subprocess` unit tests asserting exact command lines, idempotency, and stderr propagation.

### Phase 3 — State & port allocation (M)
- `state/store.py`: `StateStore` over `~/.local/share/outpost/state.json` with `write_atomic`; schema `{applied_digest, ports: {service: port}, applied_at}`.
- `engine/ports.py`: `PortAllocator` (first-fit, exclusions, exhaustion → raise).
- *DoD:* unit tests for first-fit order, exclusion of `41999`/`listen`/allocated, exhaustion, atomic write + reload round-trip.

### Phase 4 — Templates & rendering (M)
- `templates/service.j2`, `templates/nginx_server.j2`, `templates/cloudflared.j2`.
- `engine/render.py`: render a service unit (sets `WorkingDirectory=`, `Environment=`, `EnvironmentFile=`, `Restart=` + sane `RestartSec`/`StartLimit*`), NGINX server blocks (longest-prefix `location`s, upstreams), cloudflared config.
- `engine/stage.py`: write rendered files into a temp staging dir mirroring `generated/{systemd,nginx,cloudflared}`.
- *DoD:* integration/snapshot tests — given a config model, rendered unit + conf match expected text; longest-prefix routing verified.

### Phase 5 — Apply pipeline (the core) (L)
- `engine/apply.py` orchestrating the `prd.md` pipeline: parse+validate → materialize sources (clone if missing; **seed** empty `sha` via `resolve_ref` + write-back + recompute digest; `checkout(sha)`; build iff marker stale → write marker) → allocate ports → render to staging → `nginx -t` via throwaway conf → **backup** `generated/`→`.lkg/` → atomic swap → `daemon_reload` + start/restart affected → health probe per defined service → on success: `nginx reload`, ensure cloudflared started, commit `state.json` → on failure: revert from `.lkg/`, reload NGINX back, leave digest unchanged, return failure.
- `engine/health.py`: stdlib http/tcp/unix probes with timeout-polling.
- *DoD:* integration tests with mocked sysdeps: happy path; invalid NGINX config → rollback; health fail → rollback; empty-`sha` seed; idempotent no-op on digest match; build skipped on unchanged `sha`.

### Phase 6 — Update (S)
- `engine/update.py`: `fetch` → `resolve_ref(ref | --ref)` → write `ref`/`sha` into config (`write_atomic`) → `apply`. Any failure leaves the running service untouched.
- *DoD:* tests for sha-advance + apply, and for fetch/build/health failure leaving state unchanged.

### Phase 7 — CLI (M)
- `cli/app.py`: Typer app, global `--config`/`--json`; commands per `rfc.md` §19 (set must match exactly).
- Each command is thin — calls the engine and maps exceptions → exit codes (0/1/2) and `--json` output.
- `logs <service> [--lines N]` with default 200 bounded tail.
- *DoD:* `CliRunner` smoke tests for exit codes + JSON shapes; command set equals `rfc.md` §19.

### Phase 8 — MCP server (M)
- `mcp/server.py`: stdio server via the `mcp` SDK registering the 11 tools from `cli-reference.md`.
- Each tool delegates to the engine; input/output JSON shapes match `cli-reference.md`. `tail_logs` bounded; no env leakage.
- *DoD:* tests invoking tools through the server with a mocked engine; tool schemas match the reference.

### Phase 9 — init & install (M)
- `outpost init`: environment checks (git, systemd-user, nginx, cloudflared present/authed); create `outpost.yaml` + runtime dir tree; write user NGINX `nginx.conf` with the include line; enable+start NGINX + cloudflared units; verify `enable-linger`; print MCP integration guidance. Idempotent.
- `install.sh`: install binary to PATH; ensure deps (`sudo` only for missing packages, fail-fast otherwise); run `outpost init`.
- *DoD:* `init` is idempotent and sets up the include line so `apply` can render+reload; one-time setup documented.

### Phase 10 — Hardening & polish (S)
- Bounded logs and scrubbed env everywhere; error messages with remediation hints.
- End-to-end integration test: apply → update → rollback over a fake service with mocked sysdeps.
- Sync CLI help text with `cli-reference.md`; reproduce the README quickstart.
- *DoD:* `ruff`/`ty`/`pytest` green; quickstart reproducible on a clean host.

## Walking skeleton (de-risk early)

After Phase 0–1, before building the full pipeline, ship a thin vertical slice: `outpost validate` + `outpost apply --dry-run` (or a `render` debug command) that parses a real config, renders **one** service unit to stdout, and exits. This proves the model→template→engine wiring and the test harness before the subprocess-heavy work in Phase 5.

## Testing strategy

- `tests/unit/` — pure logic: model validation, digest, port allocator, atomic write, template rendering. No subprocess.
- `tests/integration/` — the apply/update state machine with **mocked sysdeps**: happy path, rollback on bad config, rollback on health fail, seed, idempotent no-op.
- `tests/mocks/` — a fake subprocess runner (records calls, returns canned `systemctl`/`git`/`nginx`/`journalctl` outputs) and a tmp-dir filesystem fixture. Engine tests never touch the real host.

Snapshot tests for rendered units/configs catch template regressions. Every validation rule and every exit-code path gets at least one test.

## Risks & open decisions

- **ty is beta.** `mypy --strict` is the documented fallback; CI should run whichever is current.
- **Systemd user-unit discovery** (symlink into `~/.config/systemd/user/` vs an alternate path) — confirm in Phase 9.
- **NGINX user-unit main config** location & include line are host-dependent; `init` must handle the common case and fail fast with remediation otherwise.
- **cloudflared auth** is operator-owned; `init` only verifies presence, not validity.
- **No concurrent locking** (`fcntl` deferred). Single-operator assumption; MCP serializes within one stdio connection only — concurrent CLI+MCP or multiple MCP clients can race. Documented, not fixed in v1.
- **Rollback depth = 1.** No history to roll back through (non-goal).
- **Build toolchains are host-provided.** Build failures surface as subprocess errors; Outpost does not manage runtimes.

## Milestones

1. **M1 — Scaffolding + models:** Phase 0–1 done. `validate` works on a real config; all rules tested.
2. **M2 — Render dry-run:** Phase 2–4 + walking skeleton. A config renders to correct units/NGINX/cloudflared text (mocked sysdeps).
3. **M3 — Apply works end-to-end (mocked):** Phase 5–6. Apply + update + rollback verified with mocked sysdeps.
4. **M4 — Operable:** Phase 7–8. Full CLI + MCP usable; exit codes and JSON shapes verified.
5. **M5 — Installable:** Phase 9–10. `curl|sh` → `init` → `up` reproducible on a clean host; README quickstart holds.
