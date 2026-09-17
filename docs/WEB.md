# OV Builder web platform

Self-service frontend for `ovbuilder`. LDAP users pick OS + environment + size,
submit, and the dedicated box runs the CLI in parallel via Celery.

## Roles

| Role | LDAP group (default) | Can |
|---|---|---|
| admin | ovbuilder-admins | everything, including VM destroy and role overrides |
| builder | ovbuilder-builders | submit builds, power/snapshot |
| viewer | ovbuilder-viewers | watch jobs |

Users never pick a datastore. `dev` maps to `YAVIN-DEV`, `prod` to `YAVIN-PROD`.

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

The `users` table is created on API startup (`create_all`). Operators who
manage schema with Alembic can also run:

```bash
cd /opt/ovbuilder/web/backend
/opt/ovbuilder/venv/bin/alembic upgrade head
```

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
- `GET /api/auth/me` — current user with the **live** effective role
- `GET /api/auth/users` — admin: list users, LDAP roles, overrides
- `PUT /api/auth/users/{username}` — admin: set or clear `role_override`
- `POST /api/builds` — queue a build
- `POST /api/builds/{id}/cancel` — stop a queued/running job
- `GET /api/inventory/{os-images,environments,clusters,networks,datacenters}`
- `GET /api/vms` — live VM list
- `POST /api/vms/{name}/power-on|power-off|reboot|snapshot`
- `DELETE /api/vms/{name}` — admin only
