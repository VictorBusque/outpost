# Outpost v1: Technology Stack & Conventions

## 1. Core Language & Toolchain

* **Language:** **Python 3.12+**
  * *Rationale:* Provides the fastest path to a robust CLI, exceptional string manipulation for config generation, and is the native language of the AI/agent ecosystem.
* **Toolchain & Distribution:** **`uv`**
  * *Rationale:* Replaces pip and virtualenv with a single, lightning-fast Rust binary. Ensures isolated, deterministic environments on the target host.

## 2. Core Libraries

* **CLI Framework:** **Typer**
  * *Rationale:* Provides an ergonomic, declarative API for building nested CLI commands. Self-documents and integrates perfectly with type hints.
* **State & Schema Validation:** **Pydantic (v2)**
  * *Rationale:* The heart of the platform's safety. Rigorously validates `outpost.yaml` against a strict schema *before* any system state is modified.
* **Config Templating:** **Jinja2**
  * *Rationale:* systemd units, NGINX server blocks, and cloudflared configs are maintained as `.j2` templates shipped with the package, populated with Pydantic model data.
* **Agent Integration:** **Model Context Protocol Python SDK (`mcp`)**
  * *Rationale:* Exposes the Typer commands over stdio so agents can safely inspect and operate the platform.
* **System Execution:** Native **`subprocess`**
  * *Rationale:* No heavy wrapper libraries. Use `subprocess.run` with `check=True` for calling `git`, `systemctl --user`, and `nginx -t`.

## 3. Code Quality & Testing (The Astral Stack)

* **Type Checker:** **`ty`**
  * *Rationale:* Astral's Rust-based type checker is 10-100x faster than traditional tools. It provides the instant LSP feedback required when heavily mutating system states and ensures strict typing across the CLI and MCP schemas.
* **Formatter & Linter:** **`ruff`**
  * *Rationale:* Replaces Black, isort, and Flake8 with a single blazing-fast binary. Ensures absolute consistency in code style and catches common execution bugs prior to testing.
* **Testing Framework:** **`pytest`**
  * *Rationale:* The core engine requires exhaustive testing due to its system-level side effects. `pytest` will be used to build a massive suite of unit tests, utilizing fixtures and `unittest.mock` to simulate systemd, Git, and NGINX behaviors without modifying the host machine during CI runs.

## 4. Codebase Architecture

```text
outpost/
├── cli/                # Typer command definitions (the human interface)
├── mcp/                # Stdio server and tool definitions (the agent interface)
├── engine/             # The core loop: validate, render, apply, update
├── models/             # Pydantic schemas (Service, Route, ConfigState)
├── templates/          # Jinja2 templates (.service, .conf, cloudflared yaml)
├── sysdeps/            # Subprocess wrappers (git, systemctl, nginx)
tests/
├── unit/               # Exhaustive logic and schema validation tests
├── integration/        # Template rendering and state machine tests
└── mocks/              # Mocked sysdeps for testing without host side-effects

```

## 5. Engineering Conventions

### Structural Safety

* **Fail-Fast & Bubble Up:** If `nginx -t` fails or a Git clone aborts, catch the `CalledProcessError`, log the exact stderr, and immediately exit the run loop with a non-zero code. Do not attempt partial recoveries.
* **Strict Typing:** Enforce `ty` in strict mode. Given the system-level mutations occurring, dynamic type drift is a critical stability risk.

### File Operations & Atomicity

* **Temporary Staging:** Never template directly into `~/.local/share/outpost/generated/`. Render configs to a secure temporary directory, run validation (`nginx -t -c <temp-dir>`), and only use `os.replace()` for atomic swaps once validated.
* **Atomic State Updates:** When modifying `state.json` or rewriting `outpost.yaml` (e.g., during `update`), write to `file.tmp` and atomic-rename to `file.ext` to prevent corruption if the machine loses power mid-write.

### Idempotency & Purity

* **Read-Only Pydantic Models:** Once `outpost.yaml` is parsed into a Pydantic model, that object is immutable. Any mutation must return a new instance of the model to be saved.
* **Idempotent Syscalls:** Wrappers around `systemctl` and `git` must be designed to safely no-op if the desired state is already met.

### Path Management

* **XDG Base Directory Strictness:** Hardcode paths relative to `os.path.expanduser("~")`:
* Config: `~/.config/outpost/`
* State/Data: `~/.local/share/outpost/`
