# OV Builder web platform

Self-service frontend for `ovbuilder`. LDAP users pick OS + environment +
compute cluster + network + size, submit, and the dedicated box runs the CLI
in parallel via Celery.

## Roles

| Role | LDAP group (default) | Can |
|---|---|---|
| admin | ovbuilder-admins | everything, including VM destroy and Settings |
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

Live cluster / network / datacenter / datastore-cluster lists talk to vCenter
through `ovbuilder.vsphere` (pyVmomi). There is no second client.

Resolve credentials in this order (`web/backend/app/vsphere_client.py`):

1. Admin **Configuration** page (runtime settings JSON on the server)
2. Web process environment / `.env`:
   - `VSPHERE_SERVER`
   - `VSPHERE_USER`
   - `VSPHERE_PASSWORD` (or `OVBUILDER_VSPHERE_PASSWORD`)
   - `VSPHERE_DATACENTER` (required when more than one datacenter is visible)
   - `VSPHERE_IGNORE_SSL` (lab default `true`)
3. CLI `config.yaml` for server / datacenter / SSL when a web field is empty

If credentials are missing or vCenter is unreachable, live inventory endpoints
return **HTTP 502** with a clear error. The Build form shows a warning and
still allows submit using config defaults. It never silently invents cluster
or network names.

CI mocks the vSphere session. No live vCenter is required for tests.

## Bare-metal install

1. `./install.sh` (CLI + Terraform modules)
2. Install Redis, Postgres, Node 20+, Nginx on the dedicated box
3. `sudo ./web/install-web.sh`
4. Edit `/opt/ovbuilder/web/backend/.env` — `DATABASE_URL`, vSphere keys
   (`VSPHERE_*`, see `.env.example`), and [Secrets and TLS](#secrets-and-tls)
5. Optionally set `environments:` in the ovbuilder `config.yaml` used by the
   service account (`OVBUILDER_HOME` / `XDG_CONFIG_HOME`)
6. Apply schema: `cd /opt/ovbuilder/web/backend && /opt/ovbuilder/venv/bin/alembic upgrade head`
7. `systemctl start ovbuilder-web ovbuilder-worker`

`install-web.sh` also runs `alembic upgrade head`. Re-run the command after changing
`.env` if Postgres was not reachable during install. The API process runs the same
upgrade on startup so a missed install step is not fatal.

## Database

Build jobs persist in Postgres (`build_jobs`). The FastAPI app and Celery workers
are separate processes and **must** share this database — an in-memory store cannot
see jobs across process boundaries or survive restarts.

| Item | Value |
|---|---|
| Env var | `DATABASE_URL` |
| Default | `postgresql+asyncpg://ovbuilder:ovbuilder@127.0.0.1:5432/ovbuilder` |
| API driver | `postgresql+asyncpg://` (async SQLAlchemy) |
| Celery / Alembic driver | `postgresql+psycopg://` (derived automatically from the same URL) |

Plain `postgresql://` and `postgresql+psycopg://` URLs are accepted; the app
normalizes them. Do not put secrets in the URL in committed files. Stored
`request` JSON is redacted (`vsphere_password` cleared); the worker receives
the password only on the Celery task payload.

### Migrations

Schema is managed with Alembic (`web/backend/alembic/`).

```bash
cd /opt/ovbuilder/web/backend   # or web/backend in a checkout
# Uses DATABASE_URL from the environment or .env
alembic upgrade head
alembic current
```

Add a new revision after changing `JobRow` / `Base.metadata`:

```bash
cd web/backend
alembic revision --autogenerate -m "describe the change"
# Review the generated file, then:
alembic upgrade head
```

The initial revision is `0001_initial_build_jobs` (`build_jobs` table).

## Terraform state

Each hostname gets its own state file:

    $XDG_DATA_HOME/ovbuilder/tfstate/<hostname>/terraform.tfstate

Parallel Celery workers do not share a state file. Do not run bare
`terraform apply` inside `/opt/ovbuilder/terraform`.

## API surface

- `POST /api/auth/login` — OAuth2 form
- `POST /api/builds` — queue a build
- `POST /api/builds/{id}/cancel` — stop a queued/running job
- `GET /api/inventory/os-images` — Packer goldens from config
- `GET /api/inventory/environments` — env → datastore-cluster mapping
- `GET /api/inventory/clusters` — live compute clusters (502 if vSphere down)
- `GET /api/inventory/networks` — live port groups (502 if vSphere down)
- `GET /api/inventory/datacenters` — live datacenters (502 if vSphere down)
- `GET /api/inventory/datastore-clusters` — live Storage DRS names
- `GET /api/inventory/datastores` — live datastores (not offered as a form picker)
- `GET /api/vms` — live VM list
- `POST /api/vms/{name}/power-on|power-off|reboot|snapshot`
- `DELETE /api/vms/{name}` — admin only
- `GET|PUT /api/settings` — admin-only vSphere + npm registry

## Secrets and TLS

### JWT `SECRET_KEY`

The API process **refuses to start** if `SECRET_KEY` is missing, empty, or
the historic example value `change-me-in-production`, unless you set
`DEBUG=true`. Debug mode mints an ephemeral key and logs a warning; tokens
do not survive a restart. Generate a real key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put it in `/opt/ovbuilder/web/backend/.env` (mode `0600`).

### vSphere password

`POST /api/builds` may accept `vsphere_password` for the worker. The field
is **omitted** from every job response model and stripped from the stored
job row. The Celery worker passes it to `ovbuilder` via `VSPHERE_PASSWORD`
/ `TF_VAR_vsphere_password` / `OVBUILDER_VSPHERE_PASSWORD` — never
`--vsphere-password` on argv (visible in `ps`). Do not log those env vars.

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
