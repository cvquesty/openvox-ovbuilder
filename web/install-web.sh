#!/usr/bin/env bash
# Bare-metal installer for the OV Builder web front end.
# Run as root on the dedicated builder box after the CLI is installed to /opt/ovbuilder.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/web"
PREFIX="${OVBUILDER_HOME:-/opt/ovbuilder}"
VENV="$PREFIX/venv"
USER_NAME="${OVBUILDER_USER:-ovbuilder}"

echo "==> Installing system packages"
if command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip python3-devel gcc postgresql-server postgresql-contrib redis nginx nodejs npm || true
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3 python3-venv python3-pip python3-dev build-essential postgresql postgresql-contrib redis-server nginx nodejs npm
fi

echo "==> Ensuring $USER_NAME user and $PREFIX"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$PREFIX" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$PREFIX" "$PREFIX/web"

echo "==> Python venv + backend deps"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install -r "$WEB/backend/requirements.txt"
"$VENV/bin/pip" install -e "$ROOT" || true

echo "==> Frontend production build"
pushd "$WEB/frontend" >/dev/null
npm ci || npm install
npm run build
popd >/dev/null
mkdir -p "$PREFIX/web/frontend/dist"
cp -a "$WEB/frontend/dist/." "$PREFIX/web/frontend/dist/"

echo "==> Backend tree + env"
mkdir -p "$PREFIX/web/backend"
cp -a "$WEB/backend/." "$PREFIX/web/backend/"
if [[ ! -f "$PREFIX/web/.env" ]]; then
  cp "$WEB/backend/.env.example" "$PREFIX/web/.env"
  echo "Wrote $PREFIX/web/.env — fill in SECRET_KEY, LDAP, and VSPHERE_* before starting."
fi

echo "==> systemd units"
install -m 644 "$WEB/systemd/ovbuilder-web.service" /etc/systemd/system/ovbuilder-web.service
install -m 644 "$WEB/systemd/ovbuilder-worker.service" /etc/systemd/system/ovbuilder-worker.service
systemctl daemon-reload

echo "==> Enable local services"
systemctl enable --now redis || systemctl enable --now redis-server || true
systemctl enable --now postgresql || true

echo "==> Done. Next:"
echo "    1. Edit $PREFIX/web/.env"
echo "    2. Create the ovbuilder Postgres role/db if needed"
echo "    3. systemctl enable --now ovbuilder-web ovbuilder-worker"
echo "    4. Point nginx at $PREFIX/web/frontend/dist and proxy /api to 127.0.0.1:8000"
