#!/usr/bin/env bash
###############################################################################
# OV Builder web stack installer (bare metal)
#
# Installs the SPA + FastAPI + Celery worker on a dedicated Linux box.
# Does NOT replace ./install.sh — that script stays CLI-only.
#
# Run as root AFTER ./install.sh has put the CLI in /opt/ovbuilder
# (or set OVBUILDER_ROOT).
#
#   sudo ./web/install-web.sh
#   sudo ./web/install-web.sh --start
#   ./web/install-web.sh --help
#   ./web/install-web.sh --self-test
###############################################################################

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${CYAN}→${NC} $*"; }
log_ok()   { echo -e "${GREEN}✔${NC} $*"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $*"; }
log_err()  { echo -e "${RED}✖${NC} $*" >&2; }
die()      { log_err "$*"; exit 1; }

ROOT="${OVBUILDER_ROOT:-/opt/ovbuilder}"
WEB_USER="${OVBUILDER_USER:-ovbuilder}"
SERVER_NAME="${OVBUILDER_SERVER_NAME:-_}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SRC="${REPO_ROOT}/web"
VENV="${ROOT}/venv"
ENV_FILE=""
SKIP_FRONTEND=false
SKIP_NGINX=false
SKIP_MIGRATE=false
START_SERVICES=false

usage() {
    cat <<'EOF'
Usage: sudo ./web/install-web.sh [options]

Bare-metal install for the OV Builder web frontend, API, and Celery worker.
Leaves the CLI-only ./install.sh unchanged.

Options:
  --start           Enable and start systemd units after install
  --skip-frontend   Do not run npm ci / npm run build
  --skip-nginx      Install units and .env only (no nginx site)
  --skip-migrate    Do not run alembic upgrade head
  --self-test       Run installer helper checks (no root, no install)
  -h, --help        Show this help

Environment:
  OVBUILDER_ROOT          Install prefix (default /opt/ovbuilder)
  OVBUILDER_USER          Service account (default ovbuilder)
  OVBUILDER_SERVER_NAME   Nginx server_name (default _)
  OVBUILDER_SECRET_KEY    Override; generated on first seed if .env key is empty
  OVBUILDER_DATABASE_URL  Override; empty DATABASE_URL fails the install
  OVBUILDER_CORS_ORIGINS  Optional JSON list written to CORS_ORIGINS
EOF
}

# --- .env helpers (used by install + --self-test) ---------------------------

env_get() {
    # env_get FILE KEY
    local file="$1" key="$2"
    python3 - "$file" "$key" <<'PY'
import sys
from pathlib import Path

path, key = sys.argv[1], sys.argv[2]
if not Path(path).is_file():
    sys.exit(0)
for raw in Path(path).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, _, value = line.partition("=")
    if name != key:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    print(value)
    break
PY
}

env_set() {
    # env_set FILE KEY VALUE  — upsert; does not quote (caller supplies raw value)
    local file="$1" key="$2" value="$3"
    python3 - "$file" "$key" "$value" <<'PY'
import sys
from pathlib import Path

path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8") if path.is_file() else ""
lines = text.splitlines()
found = False
out = []
for line in lines:
    stripped = line.lstrip()
    if stripped.startswith("#") or "=" not in line:
        out.append(line)
        continue
    name = line.split("=", 1)[0].strip()
    if name == key:
        out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)
if not found:
    if out and out[-1] != "":
        out.append("")
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

is_weak_secret() {
    local value="$1"
    case "$value" in
        ""|"change-me-in-production"|"change-me-to-a-long-random-string"|"secret"|"changeme")
            return 0
            ;;
    esac
    if [[ ${#value} -lt 32 ]]; then
        return 0
    fi
    return 1
}

generate_secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe(48))"
}

render_unit() {
    # render_unit SRC DEST — rewrite User/Group/paths for ROOT + WEB_USER
    local src="$1" dest="$2"
    python3 - "$src" "$dest" "$ROOT" "$WEB_USER" "$VENV" <<'PY'
import sys
from pathlib import Path

src, dest, root, user, venv = sys.argv[1:6]
text = Path(src).read_text(encoding="utf-8")
text = text.replace("/opt/ovbuilder/venv", venv)
text = text.replace("/opt/ovbuilder", root)
text = text.replace("User=ovbuilder", f"User={user}")
text = text.replace("Group=ovbuilder", f"Group={user}")
Path(dest).write_text(text, encoding="utf-8")
PY
}

self_test() {
    local tmp dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/ovbuilder-install-web-test.XXXXXX")"
    tmp="${dir}/.env"
    trap 'rm -rf "$dir"' RETURN

    cat >"$tmp" <<'EOF'
# comment
SECRET_KEY=
DATABASE_URL=
CORS_ORIGINS='["https://builder.example.com"]'
EOF

    [[ -z "$(env_get "$tmp" SECRET_KEY)" ]] || die "self-test: empty SECRET_KEY should read empty"
    is_weak_secret "" || die "self-test: empty secret should be weak"
    is_weak_secret "change-me-in-production" || die "self-test: example secret should be weak"
    is_weak_secret "short" || die "self-test: short secret should be weak"
    is_weak_secret "$(generate_secret)" && die "self-test: generated secret should be strong"

    env_set "$tmp" SECRET_KEY "generated-secret-value-that-is-long-enough-32+"
    [[ "$(env_get "$tmp" SECRET_KEY)" == "generated-secret-value-that-is-long-enough-32+" ]] \
        || die "self-test: env_set/get SECRET_KEY mismatch"

    env_set "$tmp" DATABASE_URL "postgresql+asyncpg://ovbuilder@127.0.0.1:5432/ovbuilder"
    [[ "$(env_get "$tmp" DATABASE_URL)" == postgresql+asyncpg://* ]] \
        || die "self-test: DATABASE_URL not written"

    grep -q "CORS_ORIGINS=" "$tmp" || die "self-test: CORS_ORIGINS lost"
    grep -q "^# comment$" "$tmp" || die "self-test: comment lost"

    local unit_src unit_dest
    unit_src="${dir}/unit.service"
    unit_dest="${dir}/unit.out"
    cat >"$unit_src" <<'EOF'
User=ovbuilder
Group=ovbuilder
WorkingDirectory=/opt/ovbuilder/web/backend
ExecStart=/opt/ovbuilder/venv/bin/uvicorn app.main:app
EOF
    ROOT=/srv/ovbuilder WEB_USER=webapp VENV=/srv/ovbuilder/venv \
        render_unit "$unit_src" "$unit_dest"
    grep -q "User=webapp" "$unit_dest" || die "self-test: unit User not rewritten"
    grep -q "/srv/ovbuilder/web/backend" "$unit_dest" || die "self-test: unit path not rewritten"
    grep -q "/srv/ovbuilder/venv/bin/uvicorn" "$unit_dest" || die "self-test: venv path not rewritten"

    log_ok "install-web.sh self-test passed"
}

# --- CLI --------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)         START_SERVICES=true; shift ;;
        --skip-frontend) SKIP_FRONTEND=true; shift ;;
        --skip-nginx)    SKIP_NGINX=true; shift ;;
        --skip-migrate)  SKIP_MIGRATE=true; shift ;;
        --self-test)     self_test; exit 0 ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "Unknown option: $1 (see --help)" ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    die "Run as root: sudo $0"
fi

ENV_FILE="${ROOT}/web/backend/.env"

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

# --- preflight --------------------------------------------------------------

log_info "Installing OV Builder web stack into ${ROOT}"

require_cmd python3
require_cmd rsync
if ! python3 -m venv --help >/dev/null 2>&1; then
    die "python3 venv module is missing (install python3-venv)"
fi

if [[ ! -x "${VENV}/bin/python" && ! -x "${VENV}/bin/ovbuilder" ]]; then
    log_warn "CLI venv not found at ${VENV}. Run ./install.sh first for a full box."
    log_warn "Continuing with a web-only venv; Celery builds need the ovbuilder CLI."
fi

if ! command -v nginx >/dev/null 2>&1; then
    if [[ "$SKIP_NGINX" == true ]]; then
        log_warn "nginx not installed (--skip-nginx set)"
    else
        die "nginx not found. Install nginx or re-run with --skip-nginx."
    fi
fi

if ! command -v psql >/dev/null 2>&1 && ! command -v pg_isready >/dev/null 2>&1; then
    log_warn "Postgres client tools not found. DATABASE_URL must still be reachable for migrations."
fi

if ! command -v redis-cli >/dev/null 2>&1; then
    log_warn "redis-cli not found. Celery needs Redis at REDIS_URL / CELERY_BROKER_URL."
elif ! redis-cli ping >/dev/null 2>&1; then
    log_warn "Redis did not answer PING. Start redis before starting ovbuilder-worker."
fi

# --- service account --------------------------------------------------------

if ! id "${WEB_USER}" >/dev/null 2>&1; then
    log_info "Creating system user ${WEB_USER}"
    # Do not --create-home against /opt/ovbuilder — install.sh already owns that tree.
    useradd --system --home-dir "${ROOT}" --shell /usr/sbin/nologin "${WEB_USER}" \
        || useradd --system --home-dir "${ROOT}" --shell /sbin/nologin "${WEB_USER}" \
        || die "Could not create system user ${WEB_USER}"
    log_ok "Created ${WEB_USER}"
else
    log_ok "Service user ${WEB_USER} already exists"
fi

install -d -m 0755 "${ROOT}"
install -d -m 0750 -o "${WEB_USER}" -g "${WEB_USER}" "${ROOT}/.config" "${ROOT}/.local/share"

# --- web tree ---------------------------------------------------------------

log_info "Syncing web tree to ${ROOT}/web"
mkdir -p "${ROOT}/web"
rsync -a --delete \
    --exclude frontend/node_modules \
    --exclude frontend/dist \
    --exclude backend/.env \
    --exclude backend/__pycache__ \
    --exclude 'backend/**/__pycache__' \
    --exclude backend/.pytest_cache \
    "${WEB_SRC}/" "${ROOT}/web/"
log_ok "Web tree synced (existing backend/.env preserved)"

# --- venv + deps ------------------------------------------------------------

if [[ ! -x "${VENV}/bin/python" ]]; then
    log_info "Creating venv at ${VENV}"
    python3 -m venv "${VENV}"
fi

log_info "Installing backend Python dependencies"
umask 022
"${VENV}/bin/pip" install --upgrade pip
"${VENV}/bin/pip" install -r "${ROOT}/web/backend/requirements.txt"
log_ok "Backend dependencies installed"

# --- .env -------------------------------------------------------------------

if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ ! -f "${ROOT}/web/backend/.env.example" ]]; then
        die "Missing ${ROOT}/web/backend/.env.example — cannot seed .env"
    fi
    log_info "Seeding ${ENV_FILE} from .env.example"
    cp "${ROOT}/web/backend/.env.example" "${ENV_FILE}"
else
    log_info "Using existing ${ENV_FILE}"
fi

# SECRET_KEY: env override, existing strong value, or generate on first seed.
# Placeholder / short values fail clearly — never boot with a known default.
secret="$(env_get "${ENV_FILE}" SECRET_KEY)"
if [[ -n "${OVBUILDER_SECRET_KEY:-}" ]]; then
    secret="${OVBUILDER_SECRET_KEY}"
    if is_weak_secret "${secret}"; then
        die "OVBUILDER_SECRET_KEY is empty, a placeholder, or shorter than 32 characters."
    fi
    env_set "${ENV_FILE}" SECRET_KEY "${secret}"
    log_ok "SECRET_KEY written from OVBUILDER_SECRET_KEY"
elif is_weak_secret "${secret}"; then
    if [[ -n "${secret}" ]]; then
        die "SECRET_KEY in ${ENV_FILE} is a placeholder or shorter than 32 characters. Set SECRET_KEY or OVBUILDER_SECRET_KEY (python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
    fi
    secret="$(generate_secret)"
    env_set "${ENV_FILE}" SECRET_KEY "${secret}"
    log_ok "Generated SECRET_KEY (32+ chars) in ${ENV_FILE}"
else
    log_ok "SECRET_KEY is set and strong"
fi

# DATABASE_URL: required. Accept env override; never silently empty.
db_url="$(env_get "${ENV_FILE}" DATABASE_URL)"
if [[ -n "${OVBUILDER_DATABASE_URL:-}" ]]; then
    db_url="${OVBUILDER_DATABASE_URL}"
    env_set "${ENV_FILE}" DATABASE_URL "${db_url}"
fi
if [[ -z "${db_url}" ]]; then
    die "DATABASE_URL is empty. Set it in ${ENV_FILE} or OVBUILDER_DATABASE_URL (example: postgresql+asyncpg://ovbuilder@127.0.0.1:5432/ovbuilder)."
fi
log_ok "DATABASE_URL is set"

# Redis / Celery defaults if the operator deleted them.
if [[ -z "$(env_get "${ENV_FILE}" REDIS_URL)" ]]; then
    env_set "${ENV_FILE}" REDIS_URL "redis://127.0.0.1:6379/0"
fi
if [[ -z "$(env_get "${ENV_FILE}" CELERY_BROKER_URL)" ]]; then
    env_set "${ENV_FILE}" CELERY_BROKER_URL "redis://127.0.0.1:6379/1"
fi
if [[ -z "$(env_get "${ENV_FILE}" CELERY_RESULT_BACKEND)" ]]; then
    env_set "${ENV_FILE}" CELERY_RESULT_BACKEND "redis://127.0.0.1:6379/2"
fi

if [[ -n "${OVBUILDER_CORS_ORIGINS:-}" ]]; then
    env_set "${ENV_FILE}" CORS_ORIGINS "${OVBUILDER_CORS_ORIGINS}"
    log_ok "CORS_ORIGINS set from OVBUILDER_CORS_ORIGINS"
elif [[ -z "$(env_get "${ENV_FILE}" CORS_ORIGINS)" ]]; then
    env_set "${ENV_FILE}" CORS_ORIGINS '["https://builder.example.com"]'
    log_info "CORS_ORIGINS defaulted to https://builder.example.com — edit before going live"
fi

# LDAP placeholders stay in the seeded file; only fill keys that vanished.
if [[ -z "$(env_get "${ENV_FILE}" LDAP_SERVER_URL)" ]]; then
    env_set "${ENV_FILE}" LDAP_SERVER_URL "ldaps://ldap.example.com:636"
fi
if [[ -z "$(env_get "${ENV_FILE}" LDAP_BIND_DN)" ]]; then
    env_set "${ENV_FILE}" LDAP_BIND_DN "cn=ovbuilder-svc,ou=services,dc=example,dc=com"
fi

chmod 600 "${ENV_FILE}"
chown "${WEB_USER}:${WEB_USER}" "${ENV_FILE}"
log_ok "Locked ${ENV_FILE} to ${WEB_USER} mode 0600"

# --- alembic ----------------------------------------------------------------

if [[ "$SKIP_MIGRATE" == true ]]; then
    log_warn "Skipping Alembic (--skip-migrate)"
elif [[ -f "${ROOT}/web/backend/alembic.ini" ]]; then
    if [[ ! -x "${VENV}/bin/alembic" ]]; then
        die "alembic is not installed in ${VENV}. Check web/backend/requirements.txt."
    fi
    log_info "Running Alembic migrations (upgrade head)"
    if ! (
        cd "${ROOT}/web/backend"
        sudo -u "${WEB_USER}" -H env \
            HOME="${ROOT}" \
            PYTHONPATH="${ROOT}/web/backend" \
            "${VENV}/bin/alembic" upgrade head
    ); then
        die "Alembic upgrade failed. Create the database and role, then re-run (see docs/WEB.md):
  sudo -u postgres createuser --pwprompt ovbuilder
  sudo -u postgres createdb -O ovbuilder ovbuilder
  cd ${ROOT}/web/backend && ${VENV}/bin/alembic upgrade head"
    fi
    log_ok "Alembic is at head"
else
    log_warn "No alembic.ini under ${ROOT}/web/backend — skipped migrations"
fi

# --- frontend ---------------------------------------------------------------

if [[ "$SKIP_FRONTEND" == true ]]; then
    log_warn "Skipping frontend build (--skip-frontend)"
    if [[ ! -f "${ROOT}/web/frontend/dist/index.html" ]]; then
        log_warn "No ${ROOT}/web/frontend/dist/index.html — nginx will 404 until you build."
    fi
else
    if ! command -v npm >/dev/null 2>&1; then
        die "npm not found. Install Node 20+ or re-run with --skip-frontend."
    fi
    log_info "Building frontend"
    (
        cd "${ROOT}/web/frontend"
        npm ci
        npm run build
    )
    log_ok "Frontend built at ${ROOT}/web/frontend/dist"
fi

# --- systemd ----------------------------------------------------------------

if ! command -v systemctl >/dev/null 2>&1; then
    die "systemctl not found. This installer targets systemd hosts."
fi

log_info "Installing systemd units"
render_unit "${ROOT}/web/systemd/ovbuilder-web.service" /tmp/ovbuilder-web.service
render_unit "${ROOT}/web/systemd/ovbuilder-worker.service" /tmp/ovbuilder-worker.service
install -m 644 /tmp/ovbuilder-web.service /etc/systemd/system/ovbuilder-web.service
install -m 644 /tmp/ovbuilder-worker.service /etc/systemd/system/ovbuilder-worker.service
rm -f /tmp/ovbuilder-web.service /tmp/ovbuilder-worker.service
systemctl daemon-reload
systemctl enable ovbuilder-web.service ovbuilder-worker.service
log_ok "Enabled ovbuilder-web and ovbuilder-worker"

# --- nginx ------------------------------------------------------------------

if [[ "$SKIP_NGINX" == false ]]; then
    local_nginx_src="${ROOT}/web/nginx/ovbuilder.conf"
    [[ -f "${local_nginx_src}" ]] || die "Missing nginx template ${local_nginx_src}"

    rendered="$(mktemp)"
    sed \
        -e "s|__ROOT__|${ROOT}|g" \
        -e "s|__SERVER_NAME__|${SERVER_NAME}|g" \
        "${local_nginx_src}" >"${rendered}"

    if [[ -d /etc/nginx/sites-available ]]; then
        install -m 644 "${rendered}" /etc/nginx/sites-available/ovbuilder
        ln -sfn /etc/nginx/sites-available/ovbuilder /etc/nginx/sites-enabled/ovbuilder
        if [[ -e /etc/nginx/sites-enabled/default ]]; then
            log_warn "Stock nginx default site is still enabled and may occupy :80. Disable it if this box is dedicated:"
            log_warn "  rm -f /etc/nginx/sites-enabled/default && nginx -t && systemctl reload nginx"
        fi
        log_ok "Installed /etc/nginx/sites-available/ovbuilder"
    elif [[ -d /etc/nginx/conf.d ]]; then
        install -m 644 "${rendered}" /etc/nginx/conf.d/ovbuilder.conf
        log_ok "Installed /etc/nginx/conf.d/ovbuilder.conf"
    else
        rm -f "${rendered}"
        die "No /etc/nginx/sites-available or /etc/nginx/conf.d. Install nginx or use --skip-nginx."
    fi
    rm -f "${rendered}"

    if nginx -t; then
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl reload nginx 2>/dev/null || systemctl restart nginx
        log_ok "Nginx configuration valid and reloaded"
    else
        die "nginx -t failed after writing the OV Builder site. Fix the config and re-run."
    fi
fi

# --- permissions ------------------------------------------------------------

log_info "Applying ownership and secret permissions"
chown -R "${WEB_USER}:${WEB_USER}" "${ROOT}/web"
# Re-lock secrets after the recursive chown (mode is preserved, be explicit).
chmod 750 "${ROOT}/web/backend"
chmod 600 "${ENV_FILE}"
chown "${WEB_USER}:${WEB_USER}" "${ENV_FILE}"
if [[ -d "${ROOT}/web/frontend/dist" ]]; then
    chmod -R a+rX "${ROOT}/web/frontend/dist"
fi
# nginx must traverse to dist; backend stays group-private.
chmod 755 "${ROOT}" "${ROOT}/web" "${ROOT}/web/frontend" || true
log_ok "Permissions applied"

# --- start ------------------------------------------------------------------

if [[ "$START_SERVICES" == true ]]; then
    log_info "Starting ovbuilder-web and ovbuilder-worker"
    systemctl start ovbuilder-web ovbuilder-worker
    systemctl --no-pager --full status ovbuilder-web ovbuilder-worker || true
fi

# NOTE: unquoted heredoc — expands ROOT / VENV / SERVER_NAME for the operator.
cat <<EOF

Web platform installed.

  Config:   ${ENV_FILE}  (mode 0600, user ${WEB_USER})
  API unit: ovbuilder-web.service     (uvicorn 127.0.0.1:4567)
  Worker:   ovbuilder-worker.service  (Celery)
  Nginx:    server_name ${SERVER_NAME} → ${ROOT}/web/frontend/dist  /api → 127.0.0.1:4567

Next:
  1. Edit ${ENV_FILE}  (LDAP bind password, CORS origin, vSphere)
  2. systemctl start ovbuilder-web ovbuilder-worker   # or re-run with --start
  3. Verify:
       curl -sS http://127.0.0.1:4567/api/health
       curl -sS http://127.0.0.1/api/health
       systemctl is-active ovbuilder-web ovbuilder-worker nginx

See docs/WEB.md.
EOF
