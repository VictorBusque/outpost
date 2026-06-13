# Outpost CLI & MCP Reference (v1)

## Purpose and authority

This is the full contract for the operator CLI and the stdio MCP server: flags, argument types, exit codes, and per-tool input/output shapes. The canonical **command set** is `rfc.md` §19 (mirrored in `prd.md` and the README table); this document refines that set with flags and I/O schemas, and adds the MCP tool contracts.

The MCP server is a thin adapter over the same internal library the CLI uses, so a tool and its CLI counterpart have identical semantics. Tool calls within a single stdio connection are serialized; there is no cross-process lock, so concurrent CLI/MCP sessions can still race on `outpost.yaml`/`state.json` (see `rfc.md` §8).

## Global options

| Option | Description |
| --- | --- |
| `-c, --config <path>` | Config file path. Default `~/.config/outpost/outpost.yaml`. |
| `--json` | Emit machine-readable JSON (read commands: `status`, `ps`, `routes`, `exposure`). |
| `-h, --help` | Per-command help. |
| `--version` | Print version and exit. |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success (or a no-op idempotent apply). |
| `1` | Operational failure — subprocess error (fail-fast), apply rollback after a failed health gate, or a service/cloudflared start failure. |
| `2` | Invalid config — schema or topology validation failure. No system mutation occurs. |

Fail-fast applies throughout: on a `subprocess` error the exact stderr is logged and the run exits non-zero; no partial recovery mid-apply.

## Commands

### `outpost init`
Bootstraps host dependencies and the platform. Verifies git/systemd-user/nginx/cloudflared, creates `outpost.yaml` and the runtime directory tree if missing, prepares the NGINX include line, and enables/starts the user NGINX and cloudflared units. Idempotent. May use `sudo` only to install missing system packages, and only if available and confirmed; otherwise it fails fast with remediation steps.

### `outpost validate`
Parses the config and runs schema + topology validation with **no** system mutation. Exits `0` if valid, `2` otherwise, printing the offending rules.

### `outpost apply`
Full reconciliation per the `prd.md` pipeline: parse/validate → materialize sources to the pinned `sha` (seeding an empty `sha` once) → generate configs to a staging dir → `nginx -t` against a throwaway `nginx.conf` that includes the staged blocks → atomic swap (last-known-good backed up) → `daemon-reload` + start/restart affected services → startup health check on each local listener → on success reload NGINX and commit the digest; on failure revert to last-known-good and reload NGINX back. Exits `1` on rollback, `2` on invalid config. Idempotent: no-op if the spec digest matches the stored applied digest and services are up.

### `outpost up`
`apply` + `systemctl --user start` on **all** services, including unchanged-but-stopped ones. The one-shot "make everything run" command and the first command on a fresh host. Idempotent.

### `outpost down`
Stops all services only. Leaves the spec, generated units, NGINX blocks, clones, and data in place so a later `up` restores the stack. Full teardown is out of scope for v1.

### `outpost update <service> [--ref <ref>]`
The **only** command that advances an existing `source.sha`. Fetches, resolves `ref` (or `--ref`, which also writes `ref`) to a new sha, writes it into `source.sha`, then runs `apply`. On fetch/build/health failure the running service is left untouched and the update is reported failed (exit `1`).

### `outpost start <service>` / `outpost stop <service>` / `outpost restart <service>`
Thin `systemctl --user` wrappers for one service. No-op when the desired state is already met.

### `outpost status`
Unified view: per-service systemd unit state, configured routes, and exposed hosts. Supports `--json`. Never echoes environment contents.

### `outpost logs <service> [--lines N]`
Bounded tail of `journalctl --user -u <service>`. `N` defaults to `200` to keep agent/terminal output manageable.

### `outpost ps`
Lists services and their systemd unit states. Supports `--json`.

### `outpost routes`
Lists configured host/path routes. Supports `--json`.

### `outpost exposure`
Lists hosts exposed through Cloudflare Tunnel. Supports `--json`.

## MCP tools

All tools accept and return JSON. None ever return environment/secret contents.

### `list_services`
- Input: `{}`
- Output: array of service summaries (same shape as `get_service_status`).

### `get_service_status`
- Input: `{ "service": "<name>" }`
- Output:
  ```json
  { "name": "api", "unit": "active", "listen": "127.0.0.1:18001",
    "sha": "b7e0d33", "ref": "main", "health": { "defined": true, "startup": "passed" },
    "since": "2026-06-13T09:12:00Z", "main_pid": 48213 }
  ```
  `unit` is the systemd state (`active`/`inactive`/`failed`/`activating`). Computed live from `systemctl --user`/journald; never persisted. `health`: `defined` is `false` when the service declares no `health` block (then `startup` is omitted), and `true` otherwise; `startup` is `"passed"`/`"failed"`/`"unknown"`, where `"unknown"` covers units that are not `active` (e.g. `inactive`/`activating`) so a health result is not implied.

### `start_service` / `stop_service` / `restart_service`
- Input: `{ "service": "<name>" }`
- Output: `{ "service": "<name>", "unit": "<new-state>" }`

### `update_service`
- Input: `{ "service": "<name>", "ref": "<optional-ref>" }`
- Output: `{ "service": "<name>", "old_sha": "...", "new_sha": "...", "applied": true }`
  On failure: `{ "service": "<name>", "applied": false, "error": "..." }` with the running service left untouched.

### `apply_config`
- Input: `{}`
- Output: `{ "applied": true, "digest": "<spec-digest>", "reverted": false, "services": [ ... ] }`
  On rollback: `{ "applied": false, "reverted": true, "error": "..." }`.

### `validate_config`
- Input: `{}`
- Output: `{ "valid": true }` or `{ "valid": false, "errors": [ { "path": "services.api.listen", "message": "..." } ] }`

### `show_routes`
- Input: `{}`
- Output: array of `{ "host": "app.example.com", "paths": [ { "prefix": "/api", "to": "api" } ] }`

### `show_exposure`
- Input: `{}`
- Output: `{ "provider": "cloudflare", "hosts": [ "app.example.com" ] }`

### `tail_logs`
- Input: `{ "service": "<name>", "lines": 200 }`  (`lines` optional, default `200`)
- Output: `{ "service": "<name>", "lines": [ "..." ] }`  — bounded tail, never the full journal.
