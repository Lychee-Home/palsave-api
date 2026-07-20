#!/usr/bin/env bash
# One-time (but safe-to-rerun) setup for palsave-api on a Linux host. Run
# from the repo root as the user the service should run under, with sudo
# available:
#
#   ./deploy/setup.sh
#
# Re-running is a no-op except where real changes are needed: it will not
# overwrite an existing .env, will not reinstall packages that are already
# present, and will not touch systemd files whose content already matches.
set -euo pipefail

PALSAVE_API_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PALSAVE_API_USER="$(whoami)"
RUNNER_USER="${RUNNER_USER:-}"
PYTHON_BIN="python3.14"

cd "$PALSAVE_API_DIR"

echo "==> Checking for $PYTHON_BIN"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "==> $PYTHON_BIN not found, installing via apt"
    sudo apt-get update
    sudo apt-get install -y python3.14 python3.14-venv
fi

echo "==> Creating virtualenv"
if [ ! -d .venv ]; then
    "$PYTHON_BIN" -m venv .venv
else
    echo "    .venv already exists, skipping"
fi

echo "==> Installing dependencies"
.venv/bin/pip install -q -r requirements.txt

echo "==> Setting up .env"
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "    Created .env from .env.example — set PALSAVE_API_BACKUP_DIR to the real backup"
    echo "    rotation path before starting the service."
else
    echo "    .env already exists, leaving it alone"
    chmod 600 .env
fi

echo "==> Checking ooz/bin/libooz.so"
if [ ! -f ooz/bin/libooz.so ]; then
    echo "    WARNING: ooz/bin/libooz.so not found. Oodle-compressed ('PlM', post-2026"
    echo "    'Summer Update') saves will fail to decompress until it's built:"
    echo "        git clone --recurse-submodules https://github.com/zao/ooz.git"
    echo "        cmake -B build -DOOZ_BUILD_EXE=OFF -DOOZ_BUILD_BUN=OFF \\"
    echo "            -DOOZ_BUILD_VALIDATE=OFF -S ooz && cmake --build build"
    echo "    then copy the resulting libooz.so to $PALSAVE_API_DIR/ooz/bin/libooz.so"
    echo "    zlib-compressed ('PlZ') saves work fine without it."
else
    echo "    Found, skipping"
fi

CI_RESTART_USER="${RUNNER_USER:-$PALSAVE_API_USER}"
echo "==> Checking passwordless sudo for 'systemctl restart palsave-api' (as $CI_RESTART_USER)"
SUDOERS_FILE="/etc/sudoers.d/palsave-api-self-restart"
SUDOERS_LINE="$CI_RESTART_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart palsave-api"
if [ "$(sudo cat "$SUDOERS_FILE" 2>/dev/null || true)" != "$SUDOERS_LINE" ]; then
    TMP_SUDOERS="$(mktemp)"
    echo "$SUDOERS_LINE" > "$TMP_SUDOERS"
    sudo visudo -cf "$TMP_SUDOERS"
    sudo install -m 440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
    rm -f "$TMP_SUDOERS"
    echo "    Installed $SUDOERS_FILE"
else
    echo "    Already configured, skipping"
fi

if [ -n "$RUNNER_USER" ]; then
    echo "==> Checking passwordless sudo for '$RUNNER_USER' to run ci-deploy.sh as $PALSAVE_API_USER"
    CI_DEPLOY_SUDOERS_FILE="/etc/sudoers.d/palsave-api-ci-deploy"
    CI_DEPLOY_SUDOERS_LINE="$RUNNER_USER ALL=($PALSAVE_API_USER) NOPASSWD: $PALSAVE_API_DIR/deploy/ci-deploy.sh"
    if [ "$(sudo cat "$CI_DEPLOY_SUDOERS_FILE" 2>/dev/null || true)" != "$CI_DEPLOY_SUDOERS_LINE" ]; then
        TMP_SUDOERS="$(mktemp)"
        echo "$CI_DEPLOY_SUDOERS_LINE" > "$TMP_SUDOERS"
        sudo visudo -cf "$TMP_SUDOERS"
        sudo install -m 440 -o root -g root "$TMP_SUDOERS" "$CI_DEPLOY_SUDOERS_FILE"
        rm -f "$TMP_SUDOERS"
        echo "    Installed $CI_DEPLOY_SUDOERS_FILE"
    else
        echo "    Already configured, skipping"
    fi

    chmod +x "$PALSAVE_API_DIR/deploy/ci-deploy.sh"
else
    echo "==> RUNNER_USER not set, skipping separate-runner sudoers setup (CI is assumed to run as $PALSAVE_API_USER)"
fi

echo "==> Installing systemd unit"
UNIT_DEST="/etc/systemd/system/palsave-api.service"
RENDERED_UNIT="$(sed -e "s#__PALSAVE_API_USER__#${PALSAVE_API_USER}#g" -e "s#__PALSAVE_API_DIR__#${PALSAVE_API_DIR}#g" deploy/palsave-api.service)"
if [ "$(sudo cat "$UNIT_DEST" 2>/dev/null || true)" != "$RENDERED_UNIT" ]; then
    echo "$RENDERED_UNIT" | sudo tee "$UNIT_DEST" >/dev/null
    sudo systemctl daemon-reload
    echo "    Installed/updated $UNIT_DEST"

    if systemctl is-active --quiet palsave-api.service; then
        read -r -p "    palsave-api.service is running and its unit file changed — restart it now? [y/N] " REPLY
        if [[ "$REPLY" =~ ^[Yy]$ ]]; then
            sudo systemctl restart palsave-api
            echo "    Restarted palsave-api.service"
        else
            echo "    Skipped restart — run 'sudo systemctl restart palsave-api' when ready"
        fi
    fi
else
    echo "    Already up to date, skipping"
fi

if ! systemctl is-enabled --quiet palsave-api.service; then
    sudo systemctl enable palsave-api.service
    echo "    Enabled palsave-api.service"
else
    echo "    palsave-api.service already enabled"
fi

echo "==> Done."
echo "Fill in .env (if you haven't already) and build ooz/bin/libooz.so if your saves need it,"
echo "then start the service with:"
echo "    sudo systemctl start palsave-api"
