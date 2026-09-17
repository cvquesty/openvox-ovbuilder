# OV Builder web platform

Self-service frontend for `ovbuilder`. LDAP users pick OS + environment + size,
submit, and the dedicated box runs the CLI in parallel via Celery.

## Roles

| Role | LDAP group (default) | Can |
|---|---|---|
| admin | ovbuilder-admins | everything, including VM destroy |
| builder | ovbuilder-builders | submit builds, power/snapshot |
| viewer | ovbuilder-viewers | watch jobs |

Users never pick a datastore. `dev` maps to `YAVIN-DEV`, `prod` to `YAVIN-PROD`.

## Bare-metal install

1. `./install.sh` (CLI + Terraform modules)
2. Install Redis, Postgres, Node 20+, Nginx on the dedicated box
3. `sudo ./web/install-web.sh`
4. Edit `/opt/ovbuilder/web/backend/.env` (LDAP, `SECRET_KEY`, `DATABASE_URL`)
5. Apply schema: `cd /opt/ovbuilder/web/backend && /opt/ovbuilder/venv/bin/alembic upgrade head`
6. `systemctl start ovbuilder-web ovbuilder-worker`

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
normalizes them. Do not put secrets in the URL in committed files.

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
- `GET /api/inventory/{os-images,environments,clusters,networks,datacenters}`
- `GET /api/vms` — live VM list
- `POST /api/vms/{name}/power-on|power-off|reboot|snapshot`
- `DELETE /api/vms/{name}` — admin only
