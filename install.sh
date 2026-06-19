#!/bin/sh
# Outpost installer — makes the host capable, then runs ``outpost init``.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/VictorBusque/outpost/main/install.sh | sh
#
# Dependencies (must be pre-installed): git, nginx, cloudflared, systemd-user.
# Privilege escalation is never used — Outpost is rootless by design.
set -e

# ── dependency check ──────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || { echo "FATAL: git not found (install git first)"; exit 1; }
command -v nginx >/dev/null 2>&1 || { echo "FATAL: nginx not found (install nginx first)"; exit 1; }
command -v cloudflared >/dev/null 2>&1 || { echo "FATAL: cloudflared not found (install cloudflared first)"; exit 1; }

# Verify systemd user mode is available.
if ! systemctl --user --version >/dev/null 2>&1; then
    echo "FATAL: systemd user mode unavailable (verify loginctl enable-linger)"
    exit 1
fi

# ── install outpost ────────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    echo "installing outpost via uv..."
    uv tool install outpost --force
elif command -v pipx >/dev/null 2>&1; then
    echo "installing outpost via pipx..."
    pipx install outpost
else
    echo "installing outpost via pip..."
    pip3 install --user outpost
fi

# ── bootstrap ──────────────────────────────────────────────────────────────
echo ""
echo "running outpost init..."
outpost init
