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
4. Edit `/opt/ovbuilder/web/backend/.env`
5. `systemctl start ovbuilder-web ovbuilder-worker`

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

## Backend tests

Pytest smokes the login contract, build RBAC, the job store, and argv/secret
redaction. They use SQLite (`aiosqlite`) and mock LDAP + Celery, so they do
not need Postgres, Redis, a directory, or the `ovbuilder` CLI.

```bash
cd web/backend
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```
