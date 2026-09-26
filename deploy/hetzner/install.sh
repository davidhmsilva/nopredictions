#!/usr/bin/env bash
# install.sh — prepare a fresh Ubuntu 24.04 Hetzner server for NOPREDICTIONS.
# Run as root, ONCE, after copying ingest/.env to the server. It installs the
# units but starts NOTHING: the Mac has to stop first, or two machines write the
# same tables and spend the same api-football key (see MIGRATION.md, step 6).
#
#   sudo bash install.sh [git-branch]
set -euo pipefail

BRANCH="${1:-main}"
REPO="https://github.com/davidhmsilva/nopredictions.git"
DIR=/opt/nopredictions
# The Mac cron interpreter is 3.9, and the code and its pins were run there.
# uv fetches that exact interpreter, so the server runs what was tested.
PY_VERSION="${PY_VERSION:-3.9}"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

echo "== packages"
apt-get update -q
apt-get install -y -q git curl ca-certificates procps findutils ufw unattended-upgrades

echo "== clock: UTC (the Mac was UTC+2; every daily time in the timers is written in UTC)"
timedatectl set-timezone UTC

echo "== user np"
id np >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash np

echo "== code -> $DIR ($BRANCH)"
if [ ! -d "$DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO" "$DIR"
else
  git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout "$BRANCH" && git -C "$DIR" pull --ff-only
fi
chown -R np:np "$DIR"

echo "== python $PY_VERSION venv at ingest/.venv (via uv)"
sudo -u np bash -lc 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
UV=/home/np/.local/bin/uv
sudo -u np "$UV" venv --python "$PY_VERSION" "$DIR/ingest/.venv"
REQ="$DIR/deploy/hetzner/requirements-mac.txt"
if [ -f "$REQ" ]; then
  echo "   using the Mac's own pip freeze"
  sudo -u np "$UV" pip install --python "$DIR/ingest/.venv/bin/python" -r "$REQ"
else
  echo "   ⚠️ no requirements-mac.txt — installing a best-effort list; run the tests"
  sudo -u np "$UV" pip install --python "$DIR/ingest/.venv/bin/python" \
    -r "$DIR/ingest/requirements.txt" numpy scipy scikit-learn pytest anthropic
fi

echo "== secrets"
if [ -f "$DIR/ingest/.env" ]; then
  chown np:np "$DIR/ingest/.env"; chmod 600 "$DIR/ingest/.env"
else
  echo "   ⚠️ $DIR/ingest/.env is missing — copy it before starting anything"
fi

echo "== systemd units (installed, NOT started)"
cp "$DIR"/deploy/hetzner/systemd/*.service "$DIR"/deploy/hetzner/systemd/*.timer /etc/systemd/system/
chmod +x "$DIR/deploy/hetzner/np-job.sh" "$DIR"/agent/*.sh
systemctl daemon-reload

echo "== log rotation"
cat > /etc/logrotate.d/nopredictions <<LR
$DIR/agent/*.log $DIR/ingest/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su np np
}
LR

echo "== firewall: ssh only (nothing here serves traffic)"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null

echo
echo "Done. Nothing is running yet. Next: MIGRATION.md step 5 (dry runs)."
