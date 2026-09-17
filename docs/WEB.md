# OV Builder web platform

Self-service frontend for `ovbuilder`. LDAP users pick OS + environment + size,
submit, and the dedicated box runs the CLI in parallel via Celery.

The CLI installer (`./install.sh`) does **not** install this stack. Laptop and
workstation users who only want `ovbuilder` can ignore this document.

## Roles

| Role | LDAP group (default) | Can |
|---|---|---|
| admin | ovbuilder-admins | everything, including VM destroy |
| builder | ovbuilder-builders | submit builds, power/snapshot |
| viewer | ovbuilder-viewers | watch jobs |

Users never pick a datastore. `dev` maps to `YAVIN-DEV`, `prod` to `YAVIN-PROD`.

## Prerequisites

Dedicated Linux host with systemd (AlmaLinux / RHEL / Ubuntu). Install the CLI
first so `/opt/ovbuilder/venv` and the Terraform modules exist.

| Package | Why |
|---|---|
| Python 3.9+ with `venv` | API + Celery (same venv as the CLI) |
| Postgres | Durable job store (`DATABASE_URL`) |
| Redis | Celery broker / result backend |
| Nginx | SPA static files + `/api` reverse proxy |
| Node 20+ / npm | Production frontend build |
| rsync | Copies `web/` into `/opt/ovbuilder/web` |

Example (Ubuntu):

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv postgresql redis-server nginx rsync
# Node 20+ from NodeSource or your distro, then:
# sudo apt-get install -y nodejs
```

Example (AlmaLinux / RHEL):

```bash
sudo dnf install -y python3 python3-pip postgresql-server redis nginx rsync
# Enable Postgres/Redis per distro docs, then install Node 20+.
```

Create the database and role before the first migrate (passwords are local-only):

```bash
sudo -u postgres createuser --pwprompt ovbuilder
sudo -u postgres createdb -O ovbuilder ovbuilder
```

## Bare-metal install

From a checkout of this repository, as root:

```bash
# 1. CLI + Terraform modules (unchanged; safe to re-run)
sudo ./install.sh

# 2. Web stack: venv deps, .env, Alembic, nginx, systemd
sudo ./web/install-web.sh
```

`install-web.sh` is idempotent: it preserves an existing
`/opt/ovbuilder/web/backend/.env`, rewrites units/nginx from the in-repo
templates, and re-runs `alembic upgrade head`.

Useful flags:

| Flag | Effect |
|---|---|
| `--start` | Enable **and** start `ovbuilder-web` / `ovbuilder-worker` |
| `--skip-frontend` | Re-run without `npm ci` / `npm run build` |
| `--skip-nginx` | Skip the site file (units + `.env` only) |
| `--skip-migrate` | Skip Alembic (fails later if the schema is missing) |
| `--self-test` | Helper checks; no root and no install |

Overrides (optional):

| Variable | Purpose |
|---|---|
| `OVBUILDER_ROOT` | Prefix (default `/opt/ovbuilder`) |
| `OVBUILDER_USER` | systemd user (default `ovbuilder`) |
| `OVBUILDER_SERVER_NAME` | nginx `server_name` (default `_`) |
| `OVBUILDER_SECRET_KEY` | JWT key if `.env` is empty |
| `OVBUILDER_DATABASE_URL` | Written when `.env` `DATABASE_URL` is empty or to override |
| `OVBUILDER_CORS_ORIGINS` | JSON list, e.g. `["https://builder.example.com"]` |

3. Edit `/opt/ovbuilder/web/backend/.env` — LDAP bind password, CORS origin,
   vSphere. `SECRET_KEY` is generated on first seed if empty. Placeholder
   values (`change-me-in-production`, keys shorter than 32 characters) fail
   the installer. An empty `DATABASE_URL` also fails.

4. Start the units (if you did not pass `--start`):

```bash
sudo systemctl start ovbuilder-web ovbuilder-worker
```

Templates live in-repo and are copied at install time:

- `web/systemd/ovbuilder-web.service` — uvicorn on `127.0.0.1:4567`
- `web/systemd/ovbuilder-worker.service` — Celery worker
- `web/nginx/ovbuilder.conf` — SPA `root` + `/api` proxy

On Debian/Ubuntu the site is `/etc/nginx/sites-available/ovbuilder`. On
RHEL-family hosts it is `/etc/nginx/conf.d/ovbuilder.conf`. If the stock
`default` site still occupies port 80, disable it:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Put TLS in front (another nginx `server` block or an external proxy). The
bundled site is HTTP-only.

## Verify health

```bash
systemctl is-active ovbuilder-web ovbuilder-worker nginx
curl -sS http://127.0.0.1:4567/api/health
curl -sS http://127.0.0.1/api/health
```

Both curls should return JSON like `{"status":"ok","app":"OpenVox OV Builder"}`.
The first hits uvicorn directly; the second goes through nginx.

If the API is down:

```bash
journalctl -u ovbuilder-web -u ovbuilder-worker -e
```

Typical causes: missing `SECRET_KEY`, Postgres not listening, or Redis down
(worker only).

## Secrets and permissions

`/opt/ovbuilder/web/backend/.env` is mode `0600`, owner `ovbuilder`. Do not
commit it. Generate a key yourself if you prefer not to use the installer
default:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

See also [SECRETS.md](SECRETS.md) (CLI golden password) and [BACKUP.md](BACKUP.md)
(Postgres dumps).

## Database

Build jobs persist in Postgres (`build_jobs`). The FastAPI app and Celery
workers **must** share this database.

| Item | Value |
|---|---|
| Env var | `DATABASE_URL` |
| Default | `postgresql+asyncpg://ovbuilder:ovbuilder@127.0.0.1:5432/ovbuilder` |

`install-web.sh` runs `alembic upgrade head` when `web/backend/alembic.ini`
is present. Re-run after changing `.env` if Postgres was down:

```bash
cd /opt/ovbuilder/web/backend
/opt/ovbuilder/venv/bin/alembic upgrade head
/opt/ovbuilder/venv/bin/alembic current
```

## Terraform state

Each hostname gets its own state file:

    $XDG_DATA_HOME/ovbuilder/tfstate/<hostname>/terraform.tfstate

On the dedicated box the worker's `HOME` is `/opt/ovbuilder`, so state lands
under `/opt/ovbuilder/.local/share/ovbuilder/tfstate/`. Parallel Celery
workers do not share a state file. Do not run bare `terraform apply` inside
`/opt/ovbuilder/terraform`.

## API surface

- `GET /api/health` — liveness
- `POST /api/auth/login` — OAuth2 form
- `POST /api/builds` — queue a build
- `POST /api/builds/{id}/cancel` — stop a queued/running job
- `GET /api/inventory/{os-images,environments,clusters,networks,datacenters}`
- `GET /api/vms` — live VM list
- `POST /api/vms/{name}/power-on|power-off|reboot|snapshot`
- `DELETE /api/vms/{name}` — admin only
