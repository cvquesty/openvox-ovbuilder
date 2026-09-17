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
4. Edit `/opt/ovbuilder/web/backend/.env` (see `.env.example` for vSphere keys)
5. Optionally set `environments:` in the ovbuilder `config.yaml` used by the
   service account (`OVBUILDER_HOME` / `XDG_CONFIG_HOME`)
6. `systemctl start ovbuilder-web ovbuilder-worker`

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
