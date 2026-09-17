#!/usr/bin/env bash
# Bare-metal install for the OV Builder web frontend + API.
# Run as root on the dedicated box AFTER ./install.sh has put the CLI in /opt/ovbuilder.
set -euo pipefail

ROOT="${OVBUILDER_ROOT:-/opt/ovbuilder}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB_SRC="${REPO_ROOT}/web"
VENV="${ROOT}/venv"
WEB_USER="${OVBUILDER_USER:-ovbuilder}"

echo "==> installing web tree into ${ROOT}/web"
mkdir -p "${ROOT}/web"
rsync -a --delete \
  --exclude frontend/node_modules \
  --exclude frontend/dist \
  --exclude backend/.env \
  --exclude backend/__pycache__ \
  --exclude 'backend/**/__pycache__' \
  "${WEB_SRC}/" "${ROOT}/web/"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "==> creating venv at ${VENV}"
  python3 -m venv "${VENV}"
fi

echo "==> installing backend Python deps"
"${VENV}/bin/pip" install --upgrade pip
"${VENV}/bin/pip" install -r "${ROOT}/web/backend/requirements.txt"

if [[ -f "${ROOT}/web/backend/.env.example" && ! -f "${ROOT}/web/backend/.env" ]]; then
  echo "==> seeding ${ROOT}/web/backend/.env from example — edit before starting services"
  cp "${ROOT}/web/backend/.env.example" "${ROOT}/web/backend/.env"
  chmod 600 "${ROOT}/web/backend/.env"
fi

echo "==> applying database migrations (alembic upgrade head)"
if ! (
  cd "${ROOT}/web/backend"
  "${VENV}/bin/alembic" upgrade head
); then
  echo "WARN: alembic upgrade failed. Start Postgres, set DATABASE_URL in ${ROOT}/web/backend/.env, then run:"
  echo "  cd ${ROOT}/web/backend && ${VENV}/bin/alembic upgrade head"
fi

echo "==> building frontend"
pushd "${ROOT}/web/frontend" >/dev/null
if command -v npm >/dev/null 2>&1; then
  npm ci
  npm run build
else
  echo "WARN: npm not found; skip frontend build. Install Node 20+ and re-run."
fi
popd >/dev/null

echo "==> installing systemd units"
install -m 644 "${ROOT}/web/systemd/ovbuilder-web.service" /etc/systemd/system/ovbuilder-web.service
install -m 644 "${ROOT}/web/systemd/ovbuilder-worker.service" /etc/systemd/system/ovbuilder-worker.service
systemctl daemon-reload
systemctl enable ovbuilder-web.service ovbuilder-worker.service

if id "${WEB_USER}" >/dev/null 2>&1; then
  chown -R "${WEB_USER}:${WEB_USER}" "${ROOT}/web" || true
fi

cat <<EOF

Web platform installed.

  1. Edit ${ROOT}/web/backend/.env  (LDAP, required SECRET_KEY, vSphere, DATABASE_URL)
  2. cd ${ROOT}/web/backend && ${VENV}/bin/alembic upgrade head
  3. systemctl start ovbuilder-web ovbuilder-worker
  4. Point nginx at ${ROOT}/web/frontend/dist and proxy /api to 127.0.0.1:4567

See docs/WEB.md.
EOF
