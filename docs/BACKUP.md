# Postgres backup for OV Builder web

Job state and web-auth user rows (LDAP role cache + local overrides) live in
the local Postgres database (`ovbuilder`).

## Nightly dump

```bash
install -d -m 0750 /var/backups/ovbuilder
cat >/etc/cron.daily/ovbuilder-pgdump <<'EOF'
#!/bin/sh
set -e
DEST=/var/backups/ovbuilder/ovbuilder-$(date +%F).sql.gz
pg_dump -U ovbuilder ovbuilder | gzip > "$DEST"
find /var/backups/ovbuilder -name 'ovbuilder-*.sql.gz' -mtime +14 -delete
EOF
chmod 755 /etc/cron.daily/ovbuilder-pgdump
```

Restore:

```bash
gunzip -c /var/backups/ovbuilder/ovbuilder-YYYY-MM-DD.sql.gz | psql -U ovbuilder ovbuilder
```

Keep `/opt/ovbuilder/web/backend/.env` and `$XDG_DATA_HOME/ovbuilder/tfstate`
on the same backup cadence — a DB restore without Terraform state cannot
reclaim VMs cleanly.
