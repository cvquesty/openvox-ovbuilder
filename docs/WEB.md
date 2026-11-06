# OV Builder web platform

Self-service frontend for `ovbuilder`. LDAP users pick OS + environment +
compute cluster + network + size, submit, and the dedicated box runs the CLI
in parallel via Celery.

Plain `./install.sh` installs only the CLI. `sudo ./install.sh --web` installs
this whole stack on a dedicated box (see [Bare-metal install](#bare-metal-install)).
Laptop and workstation users who only want `ovbuilder` can ignore this
document.

The Build form OS picker lists live `ovbuilder-*` templates from every
datacenter (name + short label only). The backend resolves the template's
source datacenter silently so SEA3 can clone goldens that live in PDXC.
`/api/inventory/clusters` lists only real `ClusterComputeResource` objects.
Standalone ESXi hosts are `/api/inventory/hosts`, labeled separately in the
compute picker.

SEA3 standalone-host placement requires a current staging install on the
web box (and on any laptop CLI). If `/opt/ovbuilder` still has a root
`data.vsphere_compute_cluster` in `providers.tf`, reinstall from tip
(`sudo ./install.sh` then `sudo ./web/install-web.sh`) before filing a
placement bug. (`sudo ./install.sh --web` does both.)

## Roles

| Role | LDAP group (default) | Can |
|---|---|---|
| admin | ovbuilder-admins | everything, including VM destroy, Settings, and role overrides |
| builder | ovbuilder-builders | submit builds, read inventory, power/snapshot |
| viewer | ovbuilder-viewers | watch jobs |

Inventory (`/api/inventory/*`) is **admin + builder only**. Viewers receive 403.

Users never pick an individual datastore LUN. They pick an **environment**
(`dev` / `prod` or whatever you configure). The backend maps that key to a
Storage DRS cluster from a **single source of truth**:

1. `environments:` in `~/.config/ovbuilder/config.yaml` (or `$XDG_CONFIG_HOME/ovbuilder/`)
2. Overlay env vars `OVBUILDER_ENV_<KEY>_DATASTORE_CLUSTER` / `_CLUSTER` / `_LABEL`
3. Built-in defaults (`dev` → `YAVIN-DEV`, `prod` → `YAVIN-PROD`)

The Celery worker uses the same mapping (`ovbuilder.placement`) when it
passes `--vm-datastore-cluster` to the CLI.

## vSphere credentials (required for live inventory)

Live cluster / host / network / datacenter / datastore-cluster lists talk to
vCenter through `ovbuilder.vsphere` (pyVmomi). There is no second client.
The clusters endpoint never returns standalone hosts.

Resolve credentials in this order (`web/backend/app/vsphere_client.py`):

1. Admin **Configuration** page (runtime settings JSON on the server)
2. Web process environment / `.env`:
   - `VSPHERE_SERVER`
   - `VSPHERE_USER`
   - `VSPHERE_PASSWORD` (or `OVBUILDER_VSPHERE_PASSWORD`)
   - `VSPHERE_DATACENTER` (required when more than one datacenter is visible)
   - `VSPHERE_IGNORE_SSL` (lab default `true`)
3. CLI `config.yaml` for server / datacenter / SSL when a web field is empty

`VSPHERE_CONNECT_TIMEOUT_SECONDS` (default `15`) bounds each vCenter socket
operation so an unreachable vCenter fails fast.

### Inventory caching

Each API process caches live inventory lists per endpoint and per vCenter
identity (server, user, datacenter; never the password). Concurrent requests
for the same list share one vCenter call.

| Setting | Default | Meaning |
|---------|---------|---------|
| `INVENTORY_CACHE_TTL_SECONDS` | `300` | Serve cached lists without calling vCenter. `0` = always live. |
| `INVENTORY_STALE_TTL_SECONDS` | `3600` | While vCenter is unreachable, keep serving the last good list this long. `0` = never stale. |
| `INVENTORY_ERROR_BACKOFF_SECONDS` | `15` | After a failed vCenter call, do not retry for this long. |

Responses carry `X-Inventory-Source` (`live`, `cache`, `stale`, or
`fallback` for config OS images) and `X-Inventory-Age` in seconds. Add
`?refresh=true` to skip the fresh cache (for example right after adding a
port group). With nothing usable cached, a vCenter failure is HTTP 502 with
the error message and, during backoff, `Retry-After`. The endpoints never
invent placeholder names.

OS images come from live vCenter (`ovbuilder-*` templates across all
datacenters). If credentials are missing or vCenter is unreachable, the
OS picker falls back to configured `ovbuilder-*` goldens so the form still
renders. Cluster, network, datacenter, and datastore-cluster lists still
return **HTTP 502** when vCenter is down.

CI mocks the vSphere session. No live vCenter is required for tests.

### How a role is chosen (every request)

The access token identifies the user. It still carries a `role` claim (issued at
login), but **the API does not trust that claim for authorization**.

On each authenticated request the server computes:

1. **Local override** in Postgres (`users.role_override`), if an admin set one.
2. Else the **LDAP group mapping**, cached for `ROLE_CACHE_TTL_SECONDS`
   (default 60). A stale cache triggers a service-account LDAP lookup — no
   user password — and stores the new `ldap_role`.
3. If LDAP is unreachable, the last stored `ldap_role` is kept (an outage is
   not treated as a revocation).
4. The JWT `role` is used only as a bootstrap hint when there is no user row
   yet *and* LDAP is down.

Group revocation (for example removing someone from `ovbuilder-admins`) is
therefore visible within about one minute, not at token expiry (default 8h).
Set `ROLE_CACHE_TTL_SECONDS=0` to re-check LDAP on every request.

`0002_users_role_overrides` creates the `users` table via
`alembic upgrade head` (install-web.sh and API startup).

### Local role overrides

Admins can pin a role for a username from **Configuration → User roles** or
the API. The change applies on the user's next request; they do not need to
sign in again.

```bash
# List users (admin bearer token)
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://builder.example.com/api/auth/users

# Pin jsmith as builder (creates the row if they have not logged in)
curl -sS -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role_override":"builder"}' \
  https://builder.example.com/api/auth/users/jsmith

# Clear the override so LDAP mapping wins again
curl -sS -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role_override":null}' \
  https://builder.example.com/api/auth/users/jsmith
```

Do not demote your only remaining admin unless another account already has
the admin override or is in `ovbuilder-admins`.

## Prerequisites

Dedicated Linux host with systemd: Ubuntu / Debian (`apt`) or AlmaLinux /
RHEL / Rocky (`dnf`). The full installer pulls everything below from the
distro repositories except Terraform.

| Package | Why |
|---|---|
| Python 3.9+ with `venv` | API + Celery (same venv as the CLI) |
| Postgres | Durable job store (`DATABASE_URL`) |
| Redis (Valkey on EL10) | Celery broker / result backend |
| Nginx | SPA static files + `/api` reverse proxy |
| Node ^20.19 or >=22.12 / npm | Production frontend build (Vite 8) |
| rsync | Copies `web/` into `/opt/ovbuilder/web` |
| Terraform 1.5+ | Worker runs `ovbuilder` → Terraform. **Not** installed for you (HashiCorp repo). |

Ubuntu 24.04 and Debian 12 ship Node 18, which is too old. The installer
uses an existing `node` on `PATH` when it is new enough, otherwise tries the
distro package and stops with instructions if that is still too old. On EL9
it enables the `nodejs:22` (or `nodejs:20`) module stream. It never pipes a
remote setup script into a shell; install Node 22 LTS from your mirror first,
or pass `--skip-frontend` and build `web/frontend` elsewhere.

## Bare-metal install

From a checkout of this repository:

```bash
sudo ./install.sh --web --start
```

That runs the CLI install (venv + Terraform modules in `/opt/ovbuilder`),
then `web/install-web.sh --with-deps --start`, which:

1. Installs Postgres, Redis, Nginx, Node, and rsync with `apt` or `dnf`
   (`postgresql-setup --initdb` on EL), then enables and starts them.
2. Creates the `ovbuilder` system user, syncs `web/` to `/opt/ovbuilder/web`,
   and installs the backend requirements into the shared venv.
3. Seeds `/opt/ovbuilder/web/backend/.env` (mode `0600`) with a generated
   `SECRET_KEY` and a generated Postgres password in `DATABASE_URL`.
4. Creates the Postgres role and database named in `DATABASE_URL` and adds
   a managed loopback password-auth block to the top of `pg_hba.conf`
   (EL defaults to `ident` for `127.0.0.1`, which rejects the app login).
5. Runs `alembic upgrade head` when `web/backend/alembic.ini` exists.
6. Builds the frontend, installs and enables `ovbuilder-web` /
   `ovbuilder-worker`, and installs the nginx site. On Debian/Ubuntu it
   disables the stock `sites-enabled/default` (the file stays in
   `sites-available`). On SELinux-enforcing hosts it sets
   `httpd_can_network_connect` and labels `frontend/dist`
   `httpd_sys_content_t`. With firewalld running it opens `http`.
7. With `--start`, (re)starts the API and worker.

Re-running is safe and is how you upgrade: packages are already present,
the existing `.env` is kept (its `DATABASE_URL` password is the source of
truth and is re-applied to the role), the `pg_hba.conf` block is only
rewritten when it changed, units and the nginx site are re-rendered from the
in-repo templates, migrations re-run, and `--start` restarts the services.

`install.sh --web` prompts once before touching system packages. Pass `-y`
for unattended runs. Everything after `--` goes to `web/install-web.sh`:

```bash
sudo ./install.sh --web -y --start -- --skip-frontend
```

To manage the packages yourself, install them first and run the two steps
separately (create the Postgres role/database and put its password in
`DATABASE_URL` in `.env` before the first migrate):

```bash
sudo ./install.sh
sudo ./web/install-web.sh --start
```

`sudo ./install.sh --uninstall` also stops and removes the two units and the
nginx site, and deletes `/opt/ovbuilder` (including `.env`; it asks first
unless `-y`). OS packages, the Postgres database, and Redis data are left in
place.

`install-web.sh` flags:

| Flag | Effect |
|---|---|
| `--with-deps` | Install + enable Postgres, Redis, Nginx, Node; create the DB role/database |
| `--skip-postgres` | With `--with-deps`: Postgres is external or a container; skip install + role creation |
| `--skip-redis` | With `--with-deps`: Redis is external or a container |
| `--start` | Enable **and** (re)start `ovbuilder-web` / `ovbuilder-worker` |
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
| `OVBUILDER_DATABASE_URL` | Written to `.env` `DATABASE_URL`. A non-local host turns off local Postgres provisioning |
| `OVBUILDER_CORS_ORIGINS` | JSON list, e.g. `["https://builder.example.com"]` |

### Optional: Postgres and Redis in containers

The API, worker, and nginx stay on the host under systemd. Only the data
services move into containers (Podman or Docker). Bind them to loopback:

```bash
sudo podman run -d --name ovbuilder-postgres --restart=always \
  -p 127.0.0.1:5432:5432 -v ovbuilder-pg:/var/lib/postgresql/data \
  -e POSTGRES_USER=ovbuilder -e POSTGRES_DB=ovbuilder \
  -e POSTGRES_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  docker.io/library/postgres:16
sudo podman run -d --name ovbuilder-redis --restart=always \
  -p 127.0.0.1:6379:6379 docker.io/library/redis:7
sudo podman exec ovbuilder-postgres printenv POSTGRES_PASSWORD   # for the URL below

sudo OVBUILDER_DATABASE_URL='postgresql+asyncpg://ovbuilder:<password>@127.0.0.1:5432/ovbuilder' \
  ./install.sh --web -y --start -- --skip-postgres --skip-redis
```

The `REDIS_URL` / `CELERY_*` defaults in `.env` already point at
`127.0.0.1:6379`. For a remote Redis, edit those three keys and re-run.
Have the container runtime (or `podman generate systemd`) bring the
containers up at boot. The units only `Wants=` `postgresql.service` /
`redis.service`, so they will not wait for containers.

### After install

1. Edit `/opt/ovbuilder/web/backend/.env` — LDAP bind password, CORS origin,
   and vSphere (`VSPHERE_SERVER` / `VSPHERE_USER` / `VSPHERE_PASSWORD` /
   `VSPHERE_DATACENTER`). See [Secrets and TLS](#secrets-and-tls) and
   `.env.example`. `SECRET_KEY` is generated on first seed if empty.
   Placeholder values (`change-me-in-production`, keys shorter than 32
   characters) fail the installer. An empty `DATABASE_URL` also fails.
   Optionally set `environments:` in the ovbuilder `config.yaml` used by the
   service account (`OVBUILDER_HOME` / `XDG_CONFIG_HOME`) so the Build form
   maps `dev`/`prod` to the right Storage DRS cluster.

2. Start the units (if you did not pass `--start`):

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
workers are separate processes and **must** share this database — an
in-memory store cannot see jobs across process boundaries or survive
restarts.

| Item | Value |
|---|---|
| Env var | `DATABASE_URL` |
| Default | `postgresql+asyncpg://ovbuilder@127.0.0.1:5432/ovbuilder` (no password in git; set one in `.env`) |
| API driver | `postgresql+asyncpg://` (async SQLAlchemy) |
| Celery / Alembic driver | `postgresql+psycopg://` (derived automatically from the same URL) |

Plain `postgresql://` and `postgresql+psycopg://` URLs are accepted; the app
normalizes them. Do not put secrets in the URL in committed files.

The API uses an async SQLAlchemy engine (`pool_size=5`, `max_overflow=10`,
`pool_pre_ping=True`). Celery workers use a **sync** engine derived from the
same URL (`asyncpg` → `psycopg`) with a smaller pool (`pool_size=2`,
`max_overflow=2`). Workers never call `asyncio.run()` to touch the database.

Submitted `request` JSON **includes** `vsphere_password` so the worker can load
the job by id. `GET`/`POST` job responses still use `BuildJobPublic` and omit
the field. Redis/Celery task args are `job_id` only.

The schema is managed only by Alembic (see below), on Postgres and on
file-backed sqlite alike; the unit tests migrate their sqlite database the
same way. Only in-memory sqlite falls back to `create_all`.

### Migrations

Schema lives in `web/backend/alembic/versions/`. Two things run
`alembic upgrade head`:

1. `install-web.sh`, on every install or upgrade (unless `--skip-migrate`).
2. API startup (`init_db()` in the FastAPI lifespan). This is a no-op when
   the database is already at head, so a missed install-time migrate is not
   fatal.

On Postgres both take a session advisory lock first, so an installer and a
starting API never migrate at the same time. Celery workers never migrate.

Run it by hand after restoring a backup, or if Postgres was down during an
install:

```bash
cd /opt/ovbuilder/web/backend   # or web/backend in a checkout
# Uses DATABASE_URL from the environment or .env
sudo -u ovbuilder /opt/ovbuilder/venv/bin/alembic upgrade head
sudo -u ovbuilder /opt/ovbuilder/venv/bin/alembic current   # expect "(head)"
```

#### Databases created before Alembic

Hosts that ran 0.97-beta19 created `build_jobs` with SQLAlchemy
`create_all`, so they have tables but no `alembic_version`. `alembic upgrade
head` adopts them automatically. It checks that the existing tables carry
every column of the matching baseline revision, stamps that revision (`0001`
for `build_jobs` only, `0002` if `users` exists too), then applies the
rest. The log shows `no alembic_version; stamping 0001`.

If the tables do not match a baseline (columns missing, a later table without
its predecessors, or a newer model's table already present), the upgrade
stops with `UnrecognizedSchemaError` and changes nothing. Fix the schema by
hand, then record the revision it matches:

```bash
sudo -u ovbuilder /opt/ovbuilder/venv/bin/alembic stamp 0002
sudo -u ovbuilder /opt/ovbuilder/venv/bin/alembic upgrade head
```

#### Adding a migration

1. Change the model in `web/backend/app/database.py` (`Base.metadata`).
2. Generate a revision against a database that is at head:

   ```bash
   cd web/backend
   DATABASE_URL=sqlite:///./dev.db alembic upgrade head
   DATABASE_URL=sqlite:///./dev.db alembic revision --autogenerate -m "add foo"
   ```

3. Rename the file to the next number (`0003_add_foo.py`), set
   `revision = "0003"`, and set `down_revision` to the **current head**
   (`alembic heads`). Review the generated operations and write a real
   `downgrade()`.
4. `PYTHONPATH=web/backend python -m pytest web/backend/tests/test_migrations.py`.
   It upgrades and downgrades on sqlite (and on Postgres when
   `OVBUILDER_TEST_POSTGRES_URL` points at a throwaway database). It fails
   if the models and migrations drift apart, or if there is more than one
   head.

Two branches that each add a revision off the same parent produce two
heads. Whichever merges second rebases and points its `down_revision` at
the other's revision (renumbering if needed). Do not ship `alembic merge`
revisions for this.

Never edit a revision that has shipped. Do not add new revisions to
`UNVERSIONED_BASELINE` in `app/migrations.py`; it describes only schemas
that existed before Alembic.

Revisions: `0001_initial_build_jobs` (`build_jobs`) and
`0002_users_role_overrides` (`users`: LDAP role cache and local overrides).

## Terraform state

Each hostname gets its own state file:

    $XDG_DATA_HOME/ovbuilder/tfstate/<hostname>/terraform.tfstate

On the dedicated box the worker's `HOME` is `/opt/ovbuilder`, so state lands
under `/opt/ovbuilder/.local/share/ovbuilder/tfstate/`. Parallel Celery
workers do not share a state file. Do not run bare `terraform apply` inside
`/opt/ovbuilder/terraform`.

## VM lifecycle

The **Virtual machines** page and `/api/vms` cover the rest of a VM's life
after a build: power on/off, guest reboot, hard reset, snapshots (create,
restore, delete), resize, leases, destroy, and reclaim.

**Who can do what.** A VM belongs to whoever ran the latest *succeeded*
build that created it. Builders see and act on their own VMs; admins see
everything and can act on any VM. A failed or cancelled build never grants
ownership, so a build cannot claim an existing VM by reusing its hostname.
Templates (`ovbuilder-*` goldens) are refused by every action.

**Leftovers of failed or cancelled builds.** A cancelled build can leave a
partly created VM in vCenter. If that VM's UUID is in the build's Terraform
state, it shows up as **partial build** for the build's owner, who can only
destroy it. Admins can destroy any VM.

**Locking and Terraform state.** Mutating actions on a VM ovbuilder built
take the same per-hostname lock as `ovbuilder build`
(`tfstate/<hostname>/.ovbuilder.lock`) and return 409 while a build of that
hostname is applying. Destroy moves the state files to
`tfstate/.retired/<hostname>-<timestamp>/` and deletes the var file
(which holds the vSphere password) and the module copy, so the next build of
that hostname starts fresh. From the CLI, use `ovbuilder destroy <hostname>`.
With a remote Terraform backend, the remote state object is left in place;
the vSphere provider drops the missing VM on the next apply.

**Resize.** vCPU and memory changes on a running VM need CPU/memory hot-add
enabled in vSphere (memory never shrinks while running); otherwise power off
first. Disks only grow, never while the VM has snapshots, and the guest
partition/filesystem must be extended inside the OS.

**Leases and reclaim.** Off by default. Settings (`.env`):

| Setting | Default | Meaning |
|---------|---------|---------|
| `VM_LEASE_DAYS` | `0` | Default lease from build completion (0 = none) |
| `VM_LEASE_MAX_DAYS` | `90` | Furthest a builder can extend their own lease |
| `VM_IDLE_DAYS` | `0` | Reclaim VMs seen powered off this long (0 = never) |
| `VM_RECLAIM_ACTION` | `power_off` | `power_off` or `destroy` |
| `VM_RECLAIM_ENABLED` | `false` | Let the hourly timer act (otherwise it only logs a dry run) |
| `VM_BUILDER_CAN_DESTROY` | `true` | Builders may destroy their own VMs |

Only ovbuilder-built VMs are ever reclaimed. `ovbuilder-reclaim.timer` runs
`python -m app.vm_lifecycle` hourly; admins can preview and run it from the
**Reclaim** button. Idle time is measured from the first sweep that sees the
VM powered off, so idle reclaim needs the timer (or previews) to run.

## API surface

- `GET /api/health` — liveness plus `db_ok`, queued/running/active counts, and queue caps
- `POST /api/auth/login` — OAuth2 form
- `GET /api/auth/me` — current user with the **live** effective role
- `GET /api/auth/users` — admin: list users, LDAP roles, overrides
- `PUT /api/auth/users/{username}` — admin: set or clear `role_override`
- `POST /api/builds` — queue a build
- `POST /api/builds/{id}/cancel` — stop a queued/running job (owner or admin; see [Cancelling builds](#cancelling-builds))
- `GET /api/inventory/os-images` — live `ovbuilder-*` templates (name + label; source DC is not returned). Config goldens if vCenter is down and nothing is cached.
- `GET /api/inventory/environments` — env → datastore-cluster mapping
- `GET /api/inventory/clusters` — live compute clusters
- `GET /api/inventory/networks` — live port groups
- `GET /api/inventory/datacenters` — live datacenters
- `GET /api/inventory/datastore-clusters` — live Storage DRS names
- `GET /api/inventory/datastores` — live datastores (not offered as a form picker)

Live inventory endpoints (everything above except `environments`) are cached;
see [Inventory caching](#inventory-caching).
- `GET /api/vms` — live VM list with owner, lease expiry, and `orphan` flag
- `POST /api/vms/{name}/power-on|power-off|reboot|reset`
- `GET|POST /api/vms/{name}/snapshots`, `POST .../snapshots/{id}/revert`, `DELETE .../snapshots/{id}`
- `POST /api/vms/{name}/resize` — `{cpus, memory_gb, disk_gb, disk_index}`
- `GET|PUT /api/vms/{name}/lease` — `{expires_at}`
- `DELETE /api/vms/{name}?confirm={name}` — destroy and retire Terraform state
- `GET /api/vms/reclaim/preview`, `POST /api/vms/reclaim` — admin
  (see [VM lifecycle](#vm-lifecycle))
- `GET|PUT /api/settings` — admin-only vSphere, npm registry, notifications
- `GET|PUT /api/auth/me/notifications` — the current user's build email preferences

## Build notifications

Every channel is **off by default**. When a build reaches `succeeded`,
`failed`, or `cancelled`, the Celery worker fans it out to whatever is
configured. Delivery errors (bad URL, SMTP down, broken template) are logged
as warnings and never change the build's status.

Configure in **Configuration → Notifications** (admin) or in `.env`. A value
saved in the UI wins; an empty UI field falls back to `.env`.

- **Events:** `NOTIFY_ON_SUCCESS` / `NOTIFY_ON_FAILURE` / `NOTIFY_ON_CANCELLED`
  gate every channel.
- **Webhook:** `NOTIFY_WEBHOOK_URL` (http/https only).
  `NOTIFY_WEBHOOK_FORMAT=slack` posts `{"text": "..."}` (Slack incoming
  webhook, Mattermost, and so on). `json` posts
  `{"event": "build.failed", "text": "...", "job": {...}}` for generic
  receivers. User-supplied fields are escaped in Slack text so a hostname
  cannot `@channel`.
- **Email:** needs `NOTIFY_EMAIL_ENABLED=true`, `SMTP_HOST`, and a sender
  (`SMTP_FROM`, else `SMTP_USER`). STARTTLS on port 587 is the default;
  `SMTP_SSL=true` with port 465 uses implicit TLS. `NOTIFY_EMAIL_OWNER=true`
  mails the requester at the directory email captured at login, if their
  preferences allow it. `NOTIFY_EMAIL_TO` adds fixed recipients, such as an
  ops list.
- **Per-user preferences:** **My notifications** (every role) turns email on
  or off for the user's own builds, per event. Defaults: success and failure
  on, cancel off. These can only narrow what the admin enabled.
- **Templates:** `NOTIFY_TEXT_TEMPLATE` (webhook text),
  `NOTIFY_EMAIL_SUBJECT_TEMPLATE`, and `NOTIFY_EMAIL_BODY_TEMPLATE` use
  `$name` placeholders (Python `string.Template`). Blank means the built-in
  default. Variables: `$status $hostname $requested_by $job_id $ip $vm_ip
  $vm_name $os_image $environment $cluster $error $created_at $started_at
  $finished_at $duration $job_url $job_link $details $app_name`. Set
  `PUBLIC_BASE_URL` to get `$job_url` links. Unknown placeholders are left
  as written.

`SMTP_PASSWORD` and the webhook URL are secrets, handled like the vSphere
password: set them in the gitignored `.env` / process environment
(`SMTP_PASSWORD` or `OVBUILDER_SMTP_PASSWORD`, `NOTIFY_WEBHOOK_URL` or
`OVBUILDER_NOTIFY_WEBHOOK_URL`), or in the admin UI, which stores them in the
0600 `runtime_settings.json` under `OVBUILDER_DATA_DIR` (outside the repo).
Never commit them. The API never returns them, only `*_set` booleans. Build passwords
are never placed in notification payloads.

## Secrets and TLS

### JWT `SECRET_KEY`

The API process **refuses to start** if `SECRET_KEY` is missing, empty, or
the historic example value `change-me-in-production`, unless you set
`DEBUG=true`. Debug mode mints an ephemeral key and logs a warning; tokens
do not survive a restart. `install-web.sh` generates a strong key on first
seed when the value is empty, and refuses placeholder / short keys.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put it in `/opt/ovbuilder/web/backend/.env` (mode `0600`).

### vSphere password

`POST /api/builds` may accept `vsphere_password` for the worker. The field is
stored on the job row so Celery can take **only** `job_id` (nothing secret in
the Redis broker payload). It is **omitted** from every job response model
(`BuildJobPublic` / `BuildRequestPublic`). The worker passes it to `ovbuilder`
via `VSPHERE_PASSWORD` / `TF_VAR_vsphere_password` / `OVBUILDER_VSPHERE_PASSWORD`
— never `--vsphere-password` on argv (visible in `ps`). Do not log those env
vars. Restrict Postgres access the same way you restrict `.env`.

Submit is rejected with HTTP 429 when the caller already has
`MAX_QUEUED_PER_USER` queued+running jobs, or when the host-wide total reaches
`MAX_CONCURRENT_BUILDS + MAX_QUEUE_DEPTH`.

### Cancelling builds

`POST /api/builds/{id}/cancel` (the job's owner or an admin) marks a queued
job `cancelled` at once and revokes its Celery task. A running job moves to
`cancelling`; the worker notices within about two seconds, even while the build
prints nothing. It sends SIGTERM to the whole build process group (ovbuilder,
`terraform`, and anything else it started) so Terraform can stop cleanly and
save its state, sends SIGKILL after `BUILD_CANCEL_GRACE_SECONDS` (default 60),
and then marks the job `cancelled`. A VM that Terraform had already started
creating may still exist in vCenter; check it before rebuilding the hostname.

### LDAP TLS

Certificate verification is **on** by default (`LDAP_SSL_VERIFY=true`).
Setting it to `false` is an explicit insecure lab opt-in and is logged.

| Setting | Purpose |
|---------|---------|
| `LDAP_SERVER_URL` | Prefer `ldaps://ldap.example.com:636` |
| `LDAP_USE_SSL` | Force LDAPS when the URL is `ldap://` |
| `LDAP_USE_STARTTLS` | Upgrade `ldap://` with `start_tls()` before bind |
| `LDAP_SSL_VERIFY` | Verify the server cert (default `true`) |
| `LDAP_CA_CERTS_FILE` | PEM bundle for a private LDAP CA |

`LDAP_CA_CERTS_FILE` is passed to ldap3 `Tls(ca_certs_file=...)`. Use it
when the directory is signed by an internal CA that is not in the host
trust store. Example: `/etc/pki/tls/certs/example-ldap-ca.pem`.

## Backend tests

Pytest covers the login contract, build RBAC, the Postgres job store
(sqlite fixture), secret redaction, and live inventory endpoints (vSphere
client mocked). LDAP and Celery are mocked; tests do not need a live
directory, Redis, Postgres, vCenter, or the `ovbuilder` CLI.

`SECRET_KEY` must be set (fail-fast unless `DEBUG=true`). The suite pins
one in `tests/conftest.py`.

```bash
cd web/backend
pip install -r requirements.txt pytest
# optional extras listed in requirements-dev.txt
# SECRET_KEY is pinned in tests/conftest.py; override via env if you want.
PYTHONPATH=. python -m pytest -q
```
