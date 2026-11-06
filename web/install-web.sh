#!/usr/bin/env bash
###############################################################################
# OV Builder web stack installer (bare metal)
#
# Installs the SPA + FastAPI + Celery worker on a dedicated Linux box.
# With --with-deps it also installs and configures Postgres, Redis, Nginx,
# and Node from the distro package manager. `sudo ./install.sh --web` runs
# the CLI install and then this script with --with-deps.
#
# Run as root AFTER ./install.sh has put the CLI in /opt/ovbuilder
# (or set OVBUILDER_ROOT).
#
#   sudo ./web/install-web.sh
#   sudo ./web/install-web.sh --with-deps --start
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
SKIP_POSTGRES=false
SKIP_REDIS=false
WITH_DEPS=false
START_SERVICES=false
NODE_MIN="20.19"

usage() {
    cat <<'EOF'
Usage: sudo ./web/install-web.sh [options]

Bare-metal install for the OV Builder web frontend, API, and Celery worker.
`sudo ./install.sh --web` runs the CLI install and then this script with
--with-deps.

Options:
  --with-deps       Install + enable Postgres, Redis, Nginx, Node (apt/dnf),
                    create the local DB role/database, and generate its
                    password into .env. Safe to re-run.
  --skip-postgres   With --with-deps: Postgres is external (or a container);
                    do not install it or create the role/database
  --skip-redis      With --with-deps: Redis is external (or a container)
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
  OVBUILDER_DATABASE_URL  Override; empty DATABASE_URL fails the install.
                          With --with-deps, a non-local host skips local
                          Postgres provisioning.
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

db_url_field() {
    # db_url_field URL user|password|host|port|database
    python3 - "$1" "$2" <<'PY'
import sys
from urllib.parse import unquote, urlsplit

u = urlsplit(sys.argv[1])
fields = {
    "user": u.username,
    "password": u.password,
    "host": u.hostname,
    "port": u.port,
    "database": u.path.lstrip("/"),
}
value = fields[sys.argv[2]]
print("" if value is None else unquote(str(value)))
PY
}

db_url_with_password() {
    # db_url_with_password URL PASSWORD — same URL with the password replaced
    python3 - "$1" "$2" <<'PY'
import sys
from urllib.parse import quote, urlsplit, urlunsplit

u = urlsplit(sys.argv[1])
host = u.hostname or ""
if ":" in host:
    host = f"[{host}]"
if u.port:
    host = f"{host}:{u.port}"
netloc = f"{u.username or ''}:{quote(sys.argv[2], safe='')}@{host}"
print(urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)))
PY
}

is_local_db_host() {
    case "$1" in
        ""|127.0.0.1|localhost|::1) return 0 ;;
    esac
    return 1
}

version_at_least() {
    # version_at_least HAVE NEED — dotted numeric compare (v prefix ignored)
    local have="${1#v}" need="${2#v}"
    [[ "$(printf '%s\n%s\n' "$need" "$have" | sort -V | head -n1)" == "$need" ]]
}

node_version_ok() {
    # Vite 8 needs Node ^20.19 || >=22.12
    local v="${1#v}"
    case "$v" in
        20.*) version_at_least "$v" "20.19" ;;
        21.*) return 1 ;;
        22.*) version_at_least "$v" "22.12" ;;
        *)    version_at_least "$v" "23.0" ;;
    esac
}

psql_quote() {
    # Value for a psql `\set name '<value>'` line (double ' and \).
    local v="$1"
    v="${v//\\/\\\\}"
    v="${v//\'/\'\'}"
    printf '%s' "$v"
}

run_as() {
    # run_as USER CMD... — prefer runuser (util-linux) so sudo is optional
    local user="$1"
    shift
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$user" -- "$@"
    else
        sudo -u "$user" -H -- "$@"
    fi
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

    local url="postgresql+asyncpg://ovbuilder@127.0.0.1:5432/ovbuilder"
    [[ "$(db_url_field "$url" user)" == "ovbuilder" ]] || die "self-test: db user parse"
    [[ "$(db_url_field "$url" host)" == "127.0.0.1" ]] || die "self-test: db host parse"
    [[ "$(db_url_field "$url" database)" == "ovbuilder" ]] || die "self-test: db name parse"
    [[ -z "$(db_url_field "$url" password)" ]] || die "self-test: empty db password parse"
    local with_pw
    with_pw="$(db_url_with_password "$url" "p@ss/w'rd")"
    [[ "$with_pw" == "postgresql+asyncpg://ovbuilder:p%40ss%2Fw%27rd@127.0.0.1:5432/ovbuilder" ]] \
        || die "self-test: db_url_with_password gave ${with_pw}"
    [[ "$(db_url_field "$with_pw" password)" == "p@ss/w'rd" ]] || die "self-test: db password round-trip"
    is_local_db_host "127.0.0.1" || die "self-test: 127.0.0.1 should be local"
    is_local_db_host "db.example.com" && die "self-test: remote host should not be local"

    node_version_ok "v22.12.0" || die "self-test: node 22.12 should pass"
    node_version_ok "v20.19.1" || die "self-test: node 20.19 should pass"
    node_version_ok "v24.1.0"  || die "self-test: node 24 should pass"
    node_version_ok "v18.19.1" && die "self-test: node 18 should fail"
    node_version_ok "v20.11.0" && die "self-test: node 20.11 should fail"
    node_version_ok "v22.3.0"  && die "self-test: node 22.3 should fail"

    [[ "$(psql_quote "a'b\\c")" == "a''b\\\\c" ]] || die "self-test: psql_quote"

    log_ok "install-web.sh self-test passed"
}

# --- CLI --------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-deps)     WITH_DEPS=true; shift ;;
        --skip-postgres) SKIP_POSTGRES=true; shift ;;
        --skip-redis)    SKIP_REDIS=true; shift ;;
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

# --- dependency provisioning (--with-deps) ----------------------------------

PKG_MGR=""
REDIS_UNIT=""

detect_pkg_mgr() {
    if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR=apt
    elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR=dnf
    elif command -v yum >/dev/null 2>&1; then
        PKG_MGR=yum
    else
        die "--with-deps supports apt (Debian/Ubuntu) and dnf/yum (RHEL family). Install Postgres, Redis, Nginx, Node 20.19+ manually and re-run without --with-deps."
    fi
}

pkg_install() {
    case "$PKG_MGR" in
        apt) DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" ;;
        dnf|yum) "$PKG_MGR" install -y -q "$@" ;;
    esac
}

unit_exists() {
    systemctl list-unit-files "$1.service" --no-legend 2>/dev/null | grep -q "^$1\.service"
}

wait_for() {
    # wait_for SECONDS DESCRIPTION CMD...
    local secs="$1" what="$2"
    shift 2
    local i
    for ((i = 0; i < secs; i++)); do
        if "$@" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    die "${what} did not become ready within ${secs}s"
}

ensure_node() {
    if command -v node >/dev/null 2>&1 && node_version_ok "$(node --version)"; then
        log_ok "Node $(node --version) satisfies >= ${NODE_MIN}"
        return 0
    fi
    log_info "Installing Node.js from the distro repositories"
    case "$PKG_MGR" in
        apt) pkg_install nodejs npm ;;
        dnf|yum)
            # EL9 ships Node 16/18 by default; newer streams are modules.
            "$PKG_MGR" module enable -y -q nodejs:22 >/dev/null 2>&1 \
                || "$PKG_MGR" module enable -y -q nodejs:20 >/dev/null 2>&1 \
                || true
            pkg_install nodejs npm
            ;;
    esac
    if ! command -v node >/dev/null 2>&1 || ! node_version_ok "$(node --version)"; then
        die "Node $(node --version 2>/dev/null || echo 'not found') is too old for the frontend build (need ^20.19 or >=22.12).
  Install Node 22 LTS (for example from https://nodejs.org or your org's mirror) and re-run,
  or re-run with --skip-frontend and build web/frontend elsewhere."
    fi
    log_ok "Node $(node --version) installed"
}

provision_packages() {
    detect_pkg_mgr
    log_info "Installing platform packages with ${PKG_MGR}"
    if [[ "$PKG_MGR" == apt ]]; then
        apt-get update -q
    fi

    local pkgs=(python3 rsync)
    case "$PKG_MGR" in
        apt) pkgs+=(python3-venv) ;;
        dnf|yum) pkgs+=(python3-pip policycoreutils-python-utils) ;;
    esac
    [[ "$SKIP_NGINX" == true ]] || pkgs+=(nginx)
    if [[ "$SKIP_POSTGRES" == false ]]; then
        case "$PKG_MGR" in
            apt) pkgs+=(postgresql postgresql-client) ;;
            dnf|yum) pkgs+=(postgresql-server postgresql) ;;
        esac
    fi
    pkg_install "${pkgs[@]}"

    if [[ "$SKIP_REDIS" == false ]]; then
        case "$PKG_MGR" in
            apt) pkg_install redis-server ;;
            # EL10 replaced redis with valkey (wire-compatible).
            dnf|yum) pkg_install redis || pkg_install valkey valkey-compat-redis ;;
        esac
    fi
    log_ok "Platform packages installed"

    [[ "$SKIP_FRONTEND" == true ]] || ensure_node

    require_cmd systemctl

    if [[ "$SKIP_POSTGRES" == false ]]; then
        if [[ "$PKG_MGR" != apt && ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
            log_info "Initializing the Postgres data directory"
            postgresql-setup --initdb
        fi
        systemctl enable --now postgresql.service
        wait_for 30 "Postgres" run_as postgres pg_isready -q
        log_ok "Postgres is running"
    fi

    if [[ "$SKIP_REDIS" == false ]]; then
        local unit
        for unit in redis-server redis valkey; do
            if unit_exists "$unit"; then
                REDIS_UNIT="$unit"
                break
            fi
        done
        [[ -n "$REDIS_UNIT" ]] || die "Redis was installed but no redis-server/redis/valkey systemd unit was found"
        systemctl enable --now "${REDIS_UNIT}.service"
        if command -v redis-cli >/dev/null 2>&1; then
            wait_for 30 "Redis" redis-cli ping
        elif command -v valkey-cli >/dev/null 2>&1; then
            wait_for 30 "Redis" valkey-cli ping
        fi
        log_ok "Redis (${REDIS_UNIT}) is running"
    fi

    if [[ "$SKIP_NGINX" == false ]]; then
        systemctl enable --now nginx.service
        log_ok "Nginx is running"
    fi
}

ensure_pg_hba() {
    # Password auth on loopback for the app role. EL initdb defaults to
    # `ident` for 127.0.0.1, which rejects the DATABASE_URL login. `md5`
    # also accepts SCRAM-hashed passwords, so it works on Postgres 13+.
    local db="$1" user="$2" hba
    hba="$(cd /tmp && run_as postgres psql -XtAc 'SHOW hba_file')"
    [[ -f "$hba" ]] || die "Could not locate pg_hba.conf (got '${hba}')"
    if python3 - "$hba" "$db" "$user" <<'PY'
import sys
from pathlib import Path

path, db, user = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
begin, end = "# BEGIN ovbuilder (managed by install-web.sh)", "# END ovbuilder"
block = [
    begin,
    f"host    {db}    {user}    127.0.0.1/32    md5",
    f"host    {db}    {user}    ::1/128         md5",
    end,
]
original = path.read_text(encoding="utf-8").splitlines()
rest = original
if begin in original and end in original:
    start, stop = original.index(begin), original.index(end)
    rest = original[:start] + original[stop + 1:]
# First matching pg_hba line wins, so the block goes at the top.
new = block + rest
if new == original:
    sys.exit(1)
path.write_text("\n".join(new) + "\n", encoding="utf-8")
PY
    then
        (cd /tmp && run_as postgres psql -XtAqc 'SELECT pg_reload_conf()') >/dev/null
        log_ok "pg_hba.conf: loopback password auth for ${user}@${db} (reloaded)"
    else
        log_ok "pg_hba.conf already allows ${user}@${db} on loopback"
    fi
}

provision_database() {
    # Create/refresh the local role + database from DATABASE_URL in .env.
    # .env is the source of truth: an empty password is generated once and
    # written back; the role password is (re)set to the .env value each run.
    local url user pw db
    url="$(env_get "${ENV_FILE}" DATABASE_URL)"
    [[ -n "$url" ]] || url="postgresql+asyncpg://ovbuilder@127.0.0.1:5432/ovbuilder"
    user="$(db_url_field "$url" user)"
    db="$(db_url_field "$url" database)"
    pw="$(db_url_field "$url" password)"
    [[ -n "$user" && -n "$db" ]] || die "DATABASE_URL in ${ENV_FILE} needs a user and database name"

    if [[ -z "$pw" ]]; then
        pw="$(generate_secret)"
        url="$(db_url_with_password "$url" "$pw")"
        env_set "${ENV_FILE}" DATABASE_URL "${url}"
        log_ok "Generated a Postgres password for ${user} into ${ENV_FILE}"
    fi

    # Values go to psql on stdin, never argv (visible in ps).
    {
        printf "\\set ON_ERROR_STOP on\n"
        printf "\\set app_user '%s'\n" "$(psql_quote "$user")"
        printf "\\set app_db '%s'\n" "$(psql_quote "$db")"
        printf "\\set app_pw '%s'\n" "$(psql_quote "$pw")"
        cat <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
ALTER ROLE :"app_user" WITH LOGIN PASSWORD :'app_pw';
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') \gexec
SQL
    } | (cd /tmp && run_as postgres psql -X -q) >/dev/null \
        || die "Could not create Postgres role/database ${user}/${db}"
    log_ok "Postgres role ${user} and database ${db} are ready"

    ensure_pg_hba "$db" "$user"
}

configure_selinux() {
    command -v getenforce >/dev/null 2>&1 || return 0
    [[ "$(getenforce 2>/dev/null)" == "Enforcing" ]] || return 0
    log_info "SELinux is enforcing — allowing nginx to proxy and serve the SPA"
    setsebool -P httpd_can_network_connect 1 || log_warn "setsebool httpd_can_network_connect failed"
    local dist="${ROOT}/web/frontend/dist"
    if command -v semanage >/dev/null 2>&1; then
        semanage fcontext -a -t httpd_sys_content_t "${dist}(/.*)?" 2>/dev/null \
            || semanage fcontext -m -t httpd_sys_content_t "${dist}(/.*)?"
        [[ -d "$dist" ]] && restorecon -R "$dist"
    elif [[ -d "$dist" ]]; then
        chcon -R -t httpd_sys_content_t "$dist" \
            || log_warn "chcon failed; nginx may get 403 on ${dist}"
    fi
    log_ok "SELinux policy applied"
}

open_firewall() {
    command -v firewall-cmd >/dev/null 2>&1 || return 0
    firewall-cmd --state >/dev/null 2>&1 || return 0
    if firewall-cmd --permanent --query-service=http >/dev/null 2>&1; then
        log_ok "firewalld already allows http"
        return 0
    fi
    firewall-cmd --permanent --add-service=http >/dev/null && firewall-cmd --reload >/dev/null
    log_ok "firewalld: opened http (80/tcp)"
}

# --- preflight --------------------------------------------------------------

log_info "Installing OV Builder web stack into ${ROOT}"

if [[ "$WITH_DEPS" == true ]]; then
    if [[ -n "${OVBUILDER_DATABASE_URL:-}" ]] \
        && ! is_local_db_host "$(db_url_field "${OVBUILDER_DATABASE_URL}" host)"; then
        log_info "OVBUILDER_DATABASE_URL points at a remote host — not provisioning local Postgres"
        SKIP_POSTGRES=true
    fi
    provision_packages
fi

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

if ! command -v terraform >/dev/null 2>&1; then
    log_warn "terraform not found in PATH. The Celery worker runs ovbuilder → Terraform; install Terraform 1.5+ (HashiCorp repo) before submitting builds."
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
# vite.config.ts reads ../../VERSION for the footer build label.
install -m 644 "${REPO_ROOT}/VERSION" "${ROOT}/VERSION"
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
if [[ "$WITH_DEPS" == true && "$SKIP_POSTGRES" == false ]]; then
    if is_local_db_host "$(db_url_field "${db_url}" host)"; then
        provision_database
        db_url="$(env_get "${ENV_FILE}" DATABASE_URL)"
    else
        log_info "DATABASE_URL host is not local — skipping role/database creation"
    fi
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
        run_as "${WEB_USER}" env \
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
render_unit "${ROOT}/web/systemd/ovbuilder-reclaim.service" /tmp/ovbuilder-reclaim.service
install -m 644 /tmp/ovbuilder-reclaim.service /etc/systemd/system/ovbuilder-reclaim.service
install -m 644 "${ROOT}/web/systemd/ovbuilder-reclaim.timer" /etc/systemd/system/ovbuilder-reclaim.timer
rm -f /tmp/ovbuilder-web.service /tmp/ovbuilder-worker.service /tmp/ovbuilder-reclaim.service
systemctl daemon-reload
systemctl enable ovbuilder-web.service ovbuilder-worker.service ovbuilder-reclaim.timer
log_ok "Enabled ovbuilder-web, ovbuilder-worker, and ovbuilder-reclaim.timer"

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
        # --with-deps means a dedicated box: the stock default_server would
        # otherwise answer :80 instead of OV Builder. The file stays in
        # sites-available.
        if [[ "$WITH_DEPS" == true && -L /etc/nginx/sites-enabled/default ]]; then
            rm -f /etc/nginx/sites-enabled/default
            log_ok "Disabled the stock nginx default site (sites-available/default kept)"
        fi
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

if [[ "$WITH_DEPS" == true && "$SKIP_NGINX" == false ]]; then
    configure_selinux
    open_firewall
fi

# --- start ------------------------------------------------------------------

if [[ "$START_SERVICES" == true ]]; then
    # restart (not start) so a re-run picks up new code and .env changes.
    log_info "Starting ovbuilder-web and ovbuilder-worker"
    systemctl restart ovbuilder-web ovbuilder-worker
    systemctl start ovbuilder-reclaim.timer
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
  2. systemctl start ovbuilder-web ovbuilder-worker ovbuilder-reclaim.timer   # or re-run with --start
  3. Verify:
       curl -sS http://127.0.0.1:4567/api/health
       curl -sS http://127.0.0.1/api/health
       systemctl is-active ovbuilder-web ovbuilder-worker nginx

See docs/WEB.md.
EOF
