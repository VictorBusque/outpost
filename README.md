# Outpost 🏕️

**A deterministic, single-node Linux micro-platform control plane.**

Outpost is a lightweight CLI and MCP (Model Context Protocol) server that turns a declarative YAML file into running `systemd` user services exposed through a managed NGINX reverse proxy and Cloudflare Tunnel.

It is designed for one machine, one configuration, and one execution loop. No schedulers, no container runtimes, no root daemons. Just your git-sourced Python/Go/Node apps running fast and lean on a VPS, Raspberry Pi, or homelab.

---

## 🎯 Why Outpost?

There is a massive gap between raw self-hosting primitives and heavy orchestration. Kubernetes and Nomad are overkill for a single node. Coolify and Dokku rely on Docker, which adds unnecessary overhead when your app is just a high-performance ASGI Python app or a compiled Go binary.

**Outpost is a deployment engine, not an orchestrator.** You define your stack in one file, and Outpost safely materializes the `systemd` units, renders the NGINX configs, checks out the exact git SHAs, and ensures everything runs as an unprivileged user.

### Features

* **Rootless by Default:** Services run as `systemd --user` units. NGINX binds to a high local port. No `sudo` required after initial host setup.
* **Git-Driven Deployments:** Every service is tied to a git repository and a specific commit SHA.
* **Built-in Ingress & Exposure:** Automatically configures a local NGINX proxy and routes public traffic via Cloudflare Tunnel.
* **Agent-Ready (MCP):** Ships with a native Model Context Protocol server over `stdio`, allowing coding agents (like Claude Code) to inspect logs, update services, and debug infrastructure safely.
* **Fast & Safe:** Written in Python, typed strictly, and managed via Astral's `uv`. Applies are atomic—if a generated NGINX config is invalid or a service fails its startup health check, Outpost reverts the system to the last known-good state.

---

## 🚀 Getting Started

### 1. Installation

Install the Outpost CLI (requires `uv`):

```bash
curl -fsSL [https://raw.githubusercontent.com/outpost-platform/outpost/main/install.sh](https://raw.githubusercontent.com/outpost-platform/outpost/main/install.sh) | sh

```

*Note: The installer may prompt for `sudo` strictly to install missing system packages (`nginx`, `cloudflared`, `git`), but Outpost itself runs entirely in user-space.*

### 2. Initialization

Bootstrap the platform on your host. This ensures systemd user services are lingering and NGINX/Cloudflare are properly configured to talk to each other:

```bash
outpost init

```

### 3. Define Your Stack

Edit your declarative config at `~/.config/outpost/outpost.yaml`:

```yaml
version: 1

services:
  api:
    source:
      git: [https://github.com/me/my-fastapi.git](https://github.com/me/my-fastapi.git)
      ref: main
    build: pip install -r requirements.txt
    command: python -m uvicorn main:app --port ${PORT}
    environment:
      LOG_LEVEL: info
      DATABASE_URL: sqlite:///${DATA_DIR}/app.db
    health:
      http: { path: /healthz }

routes:
  - host: api.example.com
    paths:
      /: { to: api }

exposure:
  cloudflare:
    credentials_file: ~/.cloudflared/app.json
    hosts: [api.example.com]

```

### 4. Deploy

Reconcile the configuration and bring the system up:

```bash
outpost up

```

To update the service to the latest commit on `main` later:

```bash
outpost update api

```

---

## 🛠️ CLI Reference

Outpost's CLI is the authoritative interface for your infrastructure.

| Command | Description |
| --- | --- |
| `outpost init` | Bootstraps the host dependencies and user-level daemon setup. |
| `outpost apply` | Reconciles the YAML to system reality (clones, builds, renders, reloads). |
| `outpost update <svc>` | Fetches the latest commit from `source.ref`, updates the SHA, and applies. |
| `outpost start/stop <svc>` | Manages the lifecycle of a specific systemd service. |
| `outpost status` | Displays unified health, route, and exposure status. |
| `outpost logs <svc>` | Tails the journald logs for a service. |
| `outpost up / down` | Brings the entire stack online or takes it offline safely. |

---

## 🤝 Contributing

We welcome contributions! Outpost is built on the [Astral stack](https://astral.sh/) to guarantee high performance, rigorous type safety, and an excellent developer experience.

### Local Development Setup

1. **Install `uv`** (our package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Clone & Sync:**

```bash
git clone [https://github.com/outpost-platform/outpost.git](https://github.com/outpost-platform/outpost.git)
cd outpost
uv sync

```

### Engineering Standards

Before submitting a Pull Request, please ensure your code adheres to the project's strict quality gates:

* **Formatting & Linting:** Run `uvx ruff check .` and `uvx ruff format .`
* **Type Checking:** We use Astral's `ty`. Run `uvx ty .` to ensure strict type compliance. No dynamic type drift is permitted in system-mutating logic.
* **Testing:** Run the test suite with `uv run pytest`. Because Outpost mutates system state, all unit tests testing the core `engine/` must mock `subprocess.run` (Git, systemctl, NGINX) and file system operations using `unittest.mock` or `pytest` fixtures.

### Architectural Rules

* **XDG Compliance:** Never hardcode paths to `~/`. Always respect `~/.config/outpost/` for user intent and `~/.local/share/outpost/` for platform state.
* **Fail-Fast:** Do not attempt partial recoveries in the engine loop. Catch `CalledProcessError` and exit cleanly.
* **Atomic Writes:** Any modifications to `state.json` or `outpost.yaml` must be written to a `.tmp` file and atomically replaced via `os.replace()`.

---

## 📄 License

Outpost is released under the [MIT License](https://www.google.com/search?q=LICENSE).
