# Alma kickstart seed

- **Source of truth:** `ks.cfg.pkrtpl` (Packer `templatefile`)
- Password is injected from **gitignored** `packer/variables.auto.pkrvars.hcl`
  (`ssh_password`). See `docs/SECRETS.md`.
